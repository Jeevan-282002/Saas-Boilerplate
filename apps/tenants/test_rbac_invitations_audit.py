import hashlib
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.projects.models import Project
from apps.subscriptions.models import Plan, Subscription
from apps.tenants.models import AuditLog, Invitation, Membership, Tenant

User = get_user_model()


@override_settings(
    ALLOW_DEFAULT_TENANT=True,
    ALLOWED_HOSTS=["testserver", "localhost", "acme.test", "beta.test"],
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "rbac-test-cache",
        }
    },
)
class RbacInvitationsAuditTests(TestCase):
    def setUp(self):
        cache.clear()

        # Plans
        self.free_plan = Plan.objects.get(code="free")

        # Tenant A (Acme)
        self.tenant_a = Tenant.objects.create(
            name="Acme Corp",
            slug="acme",
            domain="acme.test",
            database_alias="default",
            is_active=True,
            provisioning_status=Tenant.ProvisioningStatus.READY,
        )
        Subscription.objects.create(
            tenant=self.tenant_a,
            plan=self.free_plan,
            status=Subscription.Status.ACTIVE,
            starts_at=timezone.now(),
        )

        # Tenant B (Beta)
        self.tenant_b = Tenant.objects.create(
            name="Beta Dynamics",
            slug="beta",
            domain="beta.test",
            database_alias="default",
            is_active=True,
            provisioning_status=Tenant.ProvisioningStatus.READY,
        )
        Subscription.objects.create(
            tenant=self.tenant_b,
            plan=self.free_plan,
            status=Subscription.Status.ACTIVE,
            starts_at=timezone.now(),
        )

        # Users
        self.owner = User.objects.create_user(
            username="alice_owner", email="alice@acme.com", password="password"
        )
        self.admin = User.objects.create_user(
            username="bob_admin", email="bob@acme.com", password="password"
        )
        self.member = User.objects.create_user(
            username="charlie_member", email="charlie@acme.com", password="password"
        )
        self.viewer = User.objects.create_user(
            username="dave_viewer", email="dave@acme.com", password="password"
        )
        self.external_user = User.objects.create_user(
            username="eve_external", email="eve@external.com", password="password"
        )
        self.beta_user = User.objects.create_user(
            username="frank_beta", email="frank@beta.com", password="password"
        )

        # Memberships
        Membership.objects.create(user=self.owner, tenant=self.tenant_a, role=Membership.Role.OWNER)
        Membership.objects.create(user=self.admin, tenant=self.tenant_a, role=Membership.Role.ADMIN)
        Membership.objects.create(
            user=self.member, tenant=self.tenant_a, role=Membership.Role.MEMBER
        )
        Membership.objects.create(
            user=self.viewer, tenant=self.tenant_a, role=Membership.Role.VIEWER
        )
        Membership.objects.create(
            user=self.beta_user, tenant=self.tenant_b, role=Membership.Role.OWNER
        )

    def _client(self, user, tenant=None):
        client = APIClient()
        client.force_authenticate(user=user)
        t = tenant or self.tenant_a
        client.defaults["HTTP_HOST"] = t.domain
        client.defaults["HTTP_X_ORGANIZATION"] = t.slug
        return client

    # ==================== RBAC TESTS ====================

    def test_viewer_has_read_only_project_access(self):
        client = self._client(self.viewer)
        # Create project as member first
        project = Project.objects.create(
            name="Project 1", description="Description", owner=self.member.pk
        )

        # Viewer can list projects
        res = client.get("/api/projects/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data.get("results", res.data)), 1)

        # Viewer cannot create project
        res = client.post("/api/projects/", {"name": "Viewer Project"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Viewer cannot update project
        res = client.patch(f"/api/projects/{project.pk}/", {"name": "Mutated"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Viewer cannot delete project
        res = client.delete(f"/api/projects/{project.pk}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_member_can_manage_own_project_not_others(self):
        charlie_client = self._client(self.member)
        # Charlie creates own project
        res = charlie_client.post("/api/projects/", {"name": "Charlie Project"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        charlie_project_id = res.data["id"]

        # Project owned by admin
        admin_project = Project.objects.create(name="Admin Project", owner=self.admin.pk)

        # Charlie can update own project
        res = charlie_client.patch(
            f"/api/projects/{charlie_project_id}/", {"name": "Charlie Updated"}
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Charlie cannot update admin's project
        res = charlie_client.patch(
            f"/api/projects/{admin_project.pk}/", {"name": "Charlie Mutated Admin"}
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_and_owner_can_manage_all_organization_projects(self):
        project = Project.objects.create(name="Member Project", owner=self.member.pk)

        # Admin can update member project
        admin_client = self._client(self.admin)
        res = admin_client.patch(f"/api/projects/{project.pk}/", {"name": "Admin Updated"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Owner can delete member project
        owner_client = self._client(self.owner)
        res = owner_client.delete(f"/api/projects/{project.pk}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_admin_cannot_demote_or_remove_owner(self):
        admin_client = self._client(self.admin)
        owner_membership = Membership.objects.get(tenant=self.tenant_a, user=self.owner)

        # Admin tries to demote owner to member
        res = admin_client.patch(
            f"/api/members/{owner_membership.pk}/",
            {"role": Membership.Role.MEMBER},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Admin tries to remove owner
        res = admin_client.delete(f"/api/members/{owner_membership.pk}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_demote_or_remove_last_owner(self):
        owner_client = self._client(self.owner)
        owner_membership = Membership.objects.get(tenant=self.tenant_a, user=self.owner)

        # Last owner tries to demote self
        res = owner_client.patch(
            f"/api/members/{owner_membership.pk}/",
            {"role": Membership.Role.ADMIN},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("last owner", res.data.get("detail", "").lower())

        # Last owner tries to remove self
        res = owner_client.delete(f"/api/members/{owner_membership.pk}/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_cannot_promote_to_owner_when_owner_exists(self):
        admin_client = self._client(self.admin)
        member_membership = Membership.objects.get(tenant=self.tenant_a, user=self.member)

        # Admin tries to promote Charlie to Owner
        res = admin_client.patch(
            f"/api/members/{member_membership.pk}/",
            {"role": Membership.Role.OWNER},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_promote_and_transfer_owner_role(self):
        owner_client = self._client(self.owner)
        admin_membership = Membership.objects.get(tenant=self.tenant_a, user=self.admin)

        # Owner promotes Admin to Owner
        res = owner_client.patch(
            f"/api/members/{admin_membership.pk}/",
            {"role": Membership.Role.OWNER},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["role"], Membership.Role.OWNER)

        # Now there are 2 owners, original owner can demote self
        owner_membership = Membership.objects.get(tenant=self.tenant_a, user=self.owner)
        res = owner_client.patch(
            f"/api/members/{owner_membership.pk}/",
            {"role": Membership.Role.ADMIN},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    # ==================== INVITATION TESTS ====================

    def test_admin_can_invite_member_and_receive_raw_token(self):
        admin_client = self._client(self.admin)
        res = admin_client.post(
            "/api/invitations/",
            {"email": "newuser@example.com", "role": "member"},
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn("token", res.data)
        raw_token = res.data["token"]
        self.assertTrue(len(raw_token) > 20)

        # Verify hashed in DB
        expected_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        invite = Invitation.objects.get(tenant=self.tenant_a, email="newuser@example.com")
        self.assertEqual(invite.token_hash, expected_hash)
        self.assertEqual(invite.status, Invitation.Status.PENDING)

    def test_admin_cannot_invite_as_owner(self):
        admin_client = self._client(self.admin)
        res = admin_client.post(
            "/api/invitations/",
            {"email": "future_owner@example.com", "role": "owner"},
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_invite_as_owner(self):
        owner_client = self._client(self.owner)
        res = owner_client.post(
            "/api/invitations/",
            {"email": "future_owner@example.com", "role": "owner"},
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_cannot_invite_existing_member(self):
        admin_client = self._client(self.admin)
        res = admin_client.post(
            "/api/invitations/",
            {"email": "alice@acme.com", "role": "member"},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already a member", res.data.get("detail", "").lower())

    def test_cannot_create_duplicate_pending_invitation(self):
        admin_client = self._client(self.admin)
        res1 = admin_client.post(
            "/api/invitations/",
            {"email": "duplicate@example.com", "role": "member"},
        )
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        res2 = admin_client.post(
            "/api/invitations/",
            {"email": "duplicate@example.com", "role": "member"},
        )
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invitation_accept_flow_and_email_matching(self):
        admin_client = self._client(self.admin)
        invite_res = admin_client.post(
            "/api/invitations/",
            {"email": self.external_user.email, "role": "member"},
        )
        token = invite_res.data["token"]

        # Different user attempts to accept (email mismatch)
        wrong_user = User.objects.create_user(
            username="wrong_user", email="imposter@example.com", password="password"
        )
        imposter_client = APIClient()
        imposter_client.force_authenticate(user=wrong_user)
        res = imposter_client.post("/api/invitations/accept/", {"token": token})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Correct user accepts
        correct_client = APIClient()
        correct_client.force_authenticate(user=self.external_user)
        res = correct_client.post("/api/invitations/accept/", {"token": token})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Check membership created
        membership = Membership.objects.get(tenant=self.tenant_a, user=self.external_user)
        self.assertEqual(membership.role, Membership.Role.MEMBER)

        # Token cannot be reused
        res_reuse = correct_client.post("/api/invitations/accept/", {"token": token})
        self.assertEqual(res_reuse.status_code, status.HTTP_400_BAD_REQUEST)

    def test_expired_invitation_is_rejected(self):
        # Create already expired invitation
        raw_token = "expired_token_12345"
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        Invitation.objects.create(
            tenant=self.tenant_a,
            email=self.external_user.email,
            role=Membership.Role.MEMBER,
            token_hash=token_hash,
            invited_by=self.admin,
            status=Invitation.Status.PENDING,
            expires_at=timezone.now() - timedelta(hours=1),
        )

        client = APIClient()
        client.force_authenticate(user=self.external_user)
        res = client.post("/api/invitations/accept/", {"token": raw_token})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("expired", res.data.get("detail", "").lower())

    def test_admin_can_revoke_invitation(self):
        admin_client = self._client(self.admin)
        invite_res = admin_client.post(
            "/api/invitations/",
            {"email": "revokeme@example.com", "role": "member"},
        )
        invite_id = invite_res.data["id"]
        token = invite_res.data["token"]

        # Revoke
        res = admin_client.post(f"/api/invitations/{invite_id}/revoke/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], Invitation.Status.REVOKED)

        # Attempt to accept revoked invitation
        revoked_user = User.objects.create_user(
            username="revoked_user", email="revokeme@example.com", password="password"
        )
        client = APIClient()
        client.force_authenticate(user=revoked_user)
        res = client.post("/api/invitations/accept/", {"token": token})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ==================== AUDIT LOG TESTS ====================

    def test_audit_logs_recorded_on_invitation_member_and_project_lifecycle(self):
        admin_client = self._client(self.admin)

        # 1. Invite
        invite_res = admin_client.post(
            "/api/invitations/",
            {"email": self.external_user.email, "role": "member"},
        )
        token = invite_res.data["token"]
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant_a, action=AuditLog.Action.INVITATION_CREATED
            ).exists()
        )

        # 2. Accept
        ext_client = APIClient()
        ext_client.force_authenticate(user=self.external_user)
        ext_client.post("/api/invitations/accept/", {"token": token})
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant_a, action=AuditLog.Action.INVITATION_ACCEPTED
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant_a, action=AuditLog.Action.MEMBER_ADDED
            ).exists()
        )

        # 3. Project CRUD
        charlie_client = self._client(self.member)
        proj_res = charlie_client.post("/api/projects/", {"name": "Audit Test Project"})
        proj_id = proj_res.data["id"]
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant_a, action=AuditLog.Action.PROJECT_CREATED
            ).exists()
        )

        charlie_client.patch(f"/api/projects/{proj_id}/", {"name": "Audit Project Renamed"})
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant_a, action=AuditLog.Action.PROJECT_UPDATED
            ).exists()
        )

        charlie_client.delete(f"/api/projects/{proj_id}/")
        self.assertTrue(
            AuditLog.objects.filter(
                tenant=self.tenant_a, action=AuditLog.Action.PROJECT_DELETED
            ).exists()
        )

    def test_audit_logs_contain_no_sensitive_secrets(self):
        admin_client = self._client(self.admin)
        invite_res = admin_client.post(
            "/api/invitations/",
            {"email": "nosecrets@example.com", "role": "member"},
        )
        raw_token = invite_res.data["token"]

        invite_log = AuditLog.objects.get(
            tenant=self.tenant_a,
            action=AuditLog.Action.INVITATION_CREATED,
            metadata__email="nosecrets@example.com",
        )
        log_meta_str = json.dumps(invite_log.metadata)
        self.assertNotIn(raw_token, log_meta_str)
        self.assertNotIn("token", invite_log.metadata)
        self.assertNotIn("password", invite_log.metadata)

    def test_audit_logs_rbac_and_cross_tenant_isolation(self):
        # Create an audit event in Tenant A
        AuditLog.objects.create(
            tenant=self.tenant_a,
            actor=self.admin,
            action="test.action",
            resource_type="test",
        )

        # Owner and Admin can read logs
        admin_client = self._client(self.admin)
        res = admin_client.get("/api/audit-logs/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(len(res.data.get("results", res.data)) >= 1)

        owner_client = self._client(self.owner)
        res = owner_client.get("/api/audit-logs/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Member and Viewer are forbidden
        member_client = self._client(self.member)
        res = member_client.get("/api/audit-logs/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        viewer_client = self._client(self.viewer)
        res = viewer_client.get("/api/audit-logs/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Cross-tenant isolation: Beta Dynamics owner cannot see Acme logs
        beta_client = self._client(self.beta_user, tenant=self.tenant_b)
        res = beta_client.get("/api/audit-logs/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Beta Dynamics should see 0 logs
        self.assertEqual(len(res.data.get("results", res.data)), 0)
