from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.projects.models import Project
from apps.subscriptions.models import Plan, Subscription
from apps.subscriptions.services import get_current_subscription
from apps.tenants.models import Membership, Tenant


@override_settings(
    ALLOW_DEFAULT_TENANT=True,
    ALLOWED_HOSTS=["testserver", "localhost", "workspace.test", "company-a.example.com"],
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "project-api-tests",
        }
    },
)
class ProjectApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner", password="test-password")
        self.client = APIClient()
        self.tenant = Tenant.objects.create(
            name="Workspace",
            slug="workspace",
            domain="workspace.test",
            database_alias="default",
        )
        Subscription.objects.create(
            tenant=self.tenant,
            plan=Plan.objects.get(code="free"),
            status=Subscription.Status.ACTIVE,
            starts_at=timezone.now(),
        )
        self.client.defaults["HTTP_HOST"] = self.tenant.domain
        cache.clear()

    def test_authenticated_user_can_create_and_list_owned_projects(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(
            "/api/projects/",
            {"name": "Example", "description": "A tenant project"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["owner"], self.user.pk)
        self.assertEqual(Project.objects.get().name, "Example")
        self.assertEqual(self.client.get("/api/projects/").status_code, 200)

    def test_project_creation_locks_subscription_during_entitlement_check(self):
        self.client.force_authenticate(self.user)

        with patch(
            "apps.projects.views.get_current_subscription",
            wraps=get_current_subscription,
        ) as subscription_check:
            response = self.client.post(
                "/api/projects/",
                {"name": "Locked entitlement", "description": ""},
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        subscription_check.assert_called_once_with(self.tenant, for_update=True)

    def test_project_endpoints_reject_requests_without_organization_context(self):
        self.client.defaults["HTTP_HOST"] = "testserver"
        self.client.force_authenticate(self.user)

        list_response = self.client.get("/api/projects/")
        create_response = self.client.post(
            "/api/projects/",
            {"name": "Unscoped", "description": ""},
            format="json",
        )

        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(create_response.status_code, 403)
        self.assertFalse(Project.objects.filter(name="Unscoped").exists())

    def test_incomplete_organization_cannot_be_selected(self):
        self.tenant.is_active = True
        self.tenant.provisioning_status = Tenant.ProvisioningStatus.PENDING
        self.tenant.save(update_fields=["is_active", "provisioning_status"])
        Membership.objects.create(user=self.user, tenant=self.tenant)
        token = str(RefreshToken.for_user(self.user).access_token)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_X_ORGANIZATION=self.tenant.slug,
        )

        response = self.client.get("/api/projects/", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 403)
        self.client.credentials()
        self.client.force_authenticate(self.user)
        host_response = self.client.get(
            "/api/projects/",
            HTTP_HOST=self.tenant.domain,
        )
        self.assertEqual(host_response.status_code, 403)

    def test_users_only_see_their_own_projects_by_default(self):
        other_user = get_user_model().objects.create_user(
            username="other",
            password="test-password",
        )
        Project.objects.create(name="Private", owner=other_user.pk)
        self.client.force_authenticate(self.user)

        response = self.client.get("/api/projects/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [])

    def test_user_can_switch_to_another_organization_they_belong_to(self):
        host_organization = Tenant.objects.create(
            name="Company A",
            slug="company-a",
            domain="company-a.example.com",
            database_alias="default",
        )
        selected_organization = Tenant.objects.create(
            name="Company B",
            slug="company-b",
            domain="company-b.example.com",
            database_alias="default",
        )
        Membership.objects.create(user=self.user, tenant=selected_organization)
        access_token = str(RefreshToken.for_user(self.user).access_token)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
            HTTP_X_ORGANIZATION="company-b",
        )

        response = self.client.get(
            "/api/projects/",
            HTTP_HOST=host_organization.domain,
        )

        self.assertEqual(response.status_code, 200)
        organizations = self.client.get(
            "/api/organizations/",
            HTTP_HOST="localhost",
        )
        self.assertEqual(organizations.status_code, 200)
        self.assertEqual(
            [item["slug"] for item in organizations.data["results"]],
            ["company-b"],
        )

    def test_user_cannot_access_an_organization_without_membership(self):
        host_organization = Tenant.objects.create(
            name="Company A",
            slug="company-a",
            domain="company-a.example.com",
            database_alias="default",
        )
        access_token = str(RefreshToken.for_user(self.user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        response = self.client.get(
            "/api/projects/",
            HTTP_HOST=host_organization.domain,
        )

        self.assertEqual(response.status_code, 403)

    def test_user_can_obtain_and_refresh_jwt_tokens(self):
        response = self.client.post(
            "/api/auth/token/",
            {"username": "owner", "password": "test-password"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

        refresh_response = self.client.post(
            "/api/auth/token/refresh/",
            {"refresh": response.data["refresh"]},
            format="json",
        )

        self.assertEqual(refresh_response.status_code, 200)
        self.assertIn("access", refresh_response.data)
        self.assertIn("refresh", refresh_response.data)

        reused_refresh_response = self.client.post(
            "/api/auth/token/refresh/",
            {"refresh": response.data["refresh"]},
            format="json",
        )

        self.assertEqual(reused_refresh_response.status_code, 401)

    def test_invalid_credentials_do_not_issue_jwt_tokens(self):
        response = self.client.post(
            "/api/auth/token/",
            {"username": "owner", "password": "incorrect"},
            format="json",
        )

        self.assertEqual(response.status_code, 401)

    def test_jwt_obtain_is_rate_limited(self):
        responses = [
            self.client.post(
                "/api/auth/token/",
                {"username": "owner", "password": "incorrect"},
                format="json",
            )
            for _ in range(6)
        ]

        self.assertEqual([response.status_code for response in responses[:5]], [401] * 5)
        self.assertEqual(responses[5].status_code, 429)

    def test_inactive_tenant_domain_is_rejected_in_production_mode(self):
        Tenant.objects.create(
            name="Inactive Company",
            slug="inactive-company",
            domain="company-a.example.com",
            database_alias="default",
            is_active=False,
        )
        self.client.force_authenticate(self.user)

        with override_settings(ALLOW_DEFAULT_TENANT=False):
            response = self.client.get(
                "/api/projects/",
                HTTP_HOST="company-a.example.com",
            )

        self.assertEqual(response.status_code, 404)

    def test_unknown_organization_header_is_rejected(self):
        access_token = str(RefreshToken.for_user(self.user).access_token)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
            HTTP_X_ORGANIZATION="not-a-registered-organization",
        )

        response = self.client.get("/api/projects/", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 403)

    def test_user_with_change_permission_cannot_delete_another_users_project(self):
        other_user = get_user_model().objects.create_user(
            username="editor",
            password="test-password",
        )
        content_type = ContentType.objects.get_for_model(Project)
        change_perm = Permission.objects.get(
            content_type=content_type,
            codename="change_project",
        )
        view_perm = Permission.objects.get(
            content_type=content_type,
            codename="view_project",
        )
        other_user.user_permissions.add(change_perm, view_perm)
        Membership.objects.create(user=other_user, tenant=self.tenant)

        project = Project.objects.create(name="Owned Project", owner=self.user.pk)
        self.client.force_authenticate(other_user)

        patch_response = self.client.patch(
            f"/api/projects/{project.pk}/",
            {"description": "Updated by editor"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200)

        delete_response = self.client.delete(f"/api/projects/{project.pk}/")
        self.assertEqual(delete_response.status_code, 403)
        self.assertTrue(Project.objects.filter(pk=project.pk).exists())

    def test_user_with_delete_permission_can_delete_another_users_project(self):
        other_user = get_user_model().objects.create_user(
            username="admin-user",
            password="test-password",
        )
        content_type = ContentType.objects.get_for_model(Project)
        delete_perm = Permission.objects.get(
            content_type=content_type,
            codename="delete_project",
        )
        view_perm = Permission.objects.get(
            content_type=content_type,
            codename="view_project",
        )
        other_user.user_permissions.add(delete_perm, view_perm)
        Membership.objects.create(user=other_user, tenant=self.tenant)

        project = Project.objects.create(name="Owned Project 2", owner=self.user.pk)
        self.client.force_authenticate(other_user)

        delete_response = self.client.delete(f"/api/projects/{project.pk}/")
        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(Project.objects.filter(pk=project.pk).exists())

    def test_project_owner_can_delete_own_project(self):
        project = Project.objects.create(name="Owned Project 3", owner=self.user.pk)
        self.client.force_authenticate(self.user)

        delete_response = self.client.delete(f"/api/projects/{project.pk}/")
        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(Project.objects.filter(pk=project.pk).exists())
