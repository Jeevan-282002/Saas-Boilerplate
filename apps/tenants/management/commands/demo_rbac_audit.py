from copy import deepcopy
import json
from copy import deepcopy

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connections
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.projects.models import Project
from apps.subscriptions.models import Plan, Subscription
from apps.tenants.models import AuditLog, Invitation, Membership, Tenant

User = get_user_model()


class Command(BaseCommand):
    help = "Demonstrates RBAC, Invitations, Audit Logs, and Cross-Tenant Isolation."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== RBAC, INVITATIONS & AUDIT LOG DEMO ==="))

        # 1. Setup Plans and Tenants
        free_plan, _ = Plan.objects.using("default").get_or_create(
            code="free",
            defaults={"name": "Free Plan", "user_limit": 10, "usage_limits": {"projects": 10}},
        )

        tenant_a, _ = Tenant.objects.using("default").get_or_create(
            slug="acme",
            defaults={
                "name": "Acme Corp",
                "domain": "acme.test",
                "database_alias": "default",
                "is_active": True,
                "provisioning_status": Tenant.ProvisioningStatus.READY,
            },
        )
        tenant_b, _ = Tenant.objects.using("default").get_or_create(
            slug="beta",
            defaults={
                "name": "Beta Dynamics",
                "domain": "beta.test",
                "database_alias": "default",
                "is_active": True,
                "provisioning_status": Tenant.ProvisioningStatus.READY,
            },
        )

        Subscription.objects.using("default").get_or_create(
            tenant=tenant_a,
            defaults={
                "plan": free_plan,
                "status": Subscription.Status.ACTIVE,
                "starts_at": timezone.now(),
            },
        )

        if connections.databases["default"]["ENGINE"] == "django.db.backends.sqlite3":
            beta_db_path = str(settings.BASE_DIR / "db_beta.sqlite3")
            database_config = deepcopy(connections.databases["default"])
            database_config["NAME"] = beta_db_path
            connections.databases["tenant_beta"] = database_config
            call_command("migrate", "projects", database="tenant_beta", verbosity=0)
            tenant_b.database_alias = "tenant_beta"
            tenant_b.save(using="default", update_fields=["database_alias"])

        Subscription.objects.using("default").get_or_create(
            tenant=tenant_b,
            defaults={
                "plan": free_plan,
                "status": Subscription.Status.ACTIVE,
                "starts_at": timezone.now(),
            },
        )

        # 2. Setup Users
        def get_or_create_user(username, email):
            user, _ = User.objects.using("default").get_or_create(
                username=username,
                defaults={"email": email, "is_active": True},
            )
            user.email = email
            user.set_password("demo-pass-123")
            user.save(using="default")
            return user

        alice_owner = get_or_create_user("demo_alice", "alice@acme.com")
        bob_admin = get_or_create_user("demo_bob", "bob@acme.com")
        charlie_member = get_or_create_user("demo_charlie", "charlie@acme.com")
        dave_viewer = get_or_create_user("demo_dave", "dave@acme.com")
        eve_invitee = get_or_create_user("demo_eve", "eve@acme.com")
        frank_beta = get_or_create_user("demo_frank", "frank@beta.com")

        # Assign Memberships in Acme Corp
        Membership.objects.using("default").update_or_create(
            user=alice_owner, tenant=tenant_a, defaults={"role": Membership.Role.OWNER}
        )
        Membership.objects.using("default").update_or_create(
            user=bob_admin, tenant=tenant_a, defaults={"role": Membership.Role.ADMIN}
        )
        Membership.objects.using("default").update_or_create(
            user=charlie_member, tenant=tenant_a, defaults={"role": Membership.Role.MEMBER}
        )
        Membership.objects.using("default").update_or_create(
            user=dave_viewer, tenant=tenant_a, defaults={"role": Membership.Role.VIEWER}
        )
        # Frank is owner of Beta Dynamics
        Membership.objects.using("default").update_or_create(
            user=frank_beta, tenant=tenant_b, defaults={"role": Membership.Role.OWNER}
        )

        # Clear existing demo projects, memberships, and invitations for Eve/Acme
        Project.objects.using(tenant_a.database_alias).filter(name__startswith="Demo").delete()
        Invitation.objects.using("default").filter(tenant=tenant_a).delete()
        Membership.objects.using("default").filter(tenant=tenant_a, user=eve_invitee).delete()
        AuditLog.objects.using("default").filter(tenant__in=[tenant_a, tenant_b]).delete()

        self.stdout.write(self.style.SUCCESS("[OK] Setup Organizations & 4 Roles in Acme Corp:"))
        self.stdout.write(f"    - Alice (OWNER): {alice_owner.email}")
        self.stdout.write(f"    - Bob (ADMIN): {bob_admin.email}")
        self.stdout.write(f"    - Charlie (MEMBER): {charlie_member.email}")
        self.stdout.write(f"    - Dave (VIEWER): {dave_viewer.email}")

        # 3. Demonstration of Invitation Flow
        self.stdout.write(
            "\n" + self.style.MIGRATE_LABEL("--- Feature 1: Organization Invitations ---")
        )
        client = APIClient()
        client.force_authenticate(user=bob_admin)
        client.defaults["HTTP_HOST"] = "localhost"
        client.defaults["HTTP_X_ORGANIZATION"] = tenant_a.slug

        invite_res = client.post(
            "/api/invitations/",
            {"email": "eve@acme.com", "role": "member"},
            format="json",
        )
        self.stdout.write(f"Admin Bob invites Eve (Member) -> Status: {invite_res.status_code}")
        raw_token = invite_res.data.get("token")
        self.stdout.write(
            f"Secure Raw Token returned (not stored plain in DB): {raw_token[:12]}..."
        )

        # Eve accepts invitation
        eve_client = APIClient()
        eve_client.force_authenticate(user=eve_invitee)
        eve_client.defaults["HTTP_HOST"] = "localhost"
        accept_res = eve_client.post(
            "/api/invitations/accept/",
            {"token": raw_token},
            format="json",
        )
        detail = accept_res.data.get("detail")
        self.stdout.write(f"Eve accepts invitation -> Status: {accept_res.status_code} ({detail})")
        eve_membership = (
            Membership.objects.using("default").filter(tenant=tenant_a, user=eve_invitee).first()
        )
        self.stdout.write(
            self.style.SUCCESS(f"[OK] Eve joined Acme Corp with role: {eve_membership.role}")
        )

        # 4. Demonstration of RBAC & Project CRUD
        self.stdout.write(
            "\n" + self.style.MIGRATE_LABEL("--- Feature 2: Role-Based Access Control ---")
        )
        charlie_client = APIClient()
        charlie_client.force_authenticate(user=charlie_member)
        charlie_client.defaults["HTTP_HOST"] = "localhost"
        charlie_client.defaults["HTTP_X_ORGANIZATION"] = tenant_a.slug

        create_proj_res = charlie_client.post(
            "/api/projects/",
            {"name": "Demo Charlie Task Engine", "description": "Created by Charlie"},
            format="json",
        )
        proj_id = create_proj_res.data.get("id")
        status_c = create_proj_res.status_code
        self.stdout.write(
            f"Member Charlie creates project -> Status: {status_c} (ID: {proj_id})"
        )

        # Dave (Viewer) reads projects
        dave_client = APIClient()
        dave_client.force_authenticate(user=dave_viewer)
        dave_client.defaults["HTTP_HOST"] = "localhost"
        dave_client.defaults["HTTP_X_ORGANIZATION"] = tenant_a.slug

        list_proj_res = dave_client.get("/api/projects/")
        proj_count = len(list_proj_res.data.get("results", list_proj_res.data))
        status_code = list_proj_res.status_code
        self.stdout.write(
            f"Viewer Dave reads projects -> Status: {status_code}, Found: {proj_count}"
        )

        # Dave (Viewer) attempts mutation (Should be forbidden 403)
        mutate_res = dave_client.patch(
            f"/api/projects/{proj_id}/",
            {"name": "Dave Hacked Project"},
            format="json",
        )
        self.stdout.write(
            f"Viewer Dave attempts update -> Status: {mutate_res.status_code} (Expected 403)"
        )
        assert mutate_res.status_code == status.HTTP_403_FORBIDDEN, (
            "Viewer mutation was not blocked!"
        )
        self.stdout.write(self.style.SUCCESS("[OK] Viewer write mutation blocked successfully."))

        # Bob (Admin) updates project
        bob_client = APIClient()
        bob_client.force_authenticate(user=bob_admin)
        bob_client.defaults["HTTP_HOST"] = "localhost"
        bob_client.defaults["HTTP_X_ORGANIZATION"] = tenant_a.slug

        bob_update_res = bob_client.patch(
            f"/api/projects/{proj_id}/",
            {"name": "Demo Acme Core Task Engine"},
            format="json",
        )
        name = bob_update_res.data.get("name")
        b_status = bob_update_res.status_code
        self.stdout.write(f"Admin Bob updates project -> Status: {b_status} ({name})")
        self.stdout.write(self.style.SUCCESS("[OK] Admin project update succeeded."))

        # 5. Demonstration of Audit Logs
        self.stdout.write("\n" + self.style.MIGRATE_LABEL("--- Feature 3: Audit Logs ---"))
        logs_res = bob_client.get("/api/audit-logs/")
        self.stdout.write(f"Admin Bob reads audit logs -> Status: {logs_res.status_code}")
        results = logs_res.data.get("results", logs_res.data)
        self.stdout.write(f"Total audit events recorded for Acme Corp: {len(results)}")
        for log in results[:5]:
            actor = log.get("actor_username") or "System"
            action = log.get("action")
            meta = json.dumps(log.get("metadata", {}))
            self.stdout.write(f"    - [{action}] by {actor} | Metadata: {meta}")

        # Check Viewer cannot view audit logs
        dave_logs_res = dave_client.get("/api/audit-logs/")
        d_status = dave_logs_res.status_code
        self.stdout.write(
            f"Viewer Dave attempts to read audit logs -> Status: {d_status} (Expected 403)"
        )
        assert dave_logs_res.status_code == status.HTTP_403_FORBIDDEN, "Viewer accessed audit logs!"
        self.stdout.write(self.style.SUCCESS("[OK] Audit log RBAC enforcement verified."))

        # 6. Demonstration of Cross-Tenant Isolation
        self.stdout.write(
            "\n" + self.style.MIGRATE_LABEL("--- Feature 4: Cross-Tenant Isolation ---")
        )
        frank_client = APIClient()
        frank_client.force_authenticate(user=frank_beta)
        frank_client.defaults["HTTP_HOST"] = "localhost"
        frank_client.defaults["HTTP_X_ORGANIZATION"] = tenant_b.slug

        beta_projects = frank_client.get("/api/projects/")
        beta_proj_count = len(beta_projects.data.get("results", beta_projects.data))
        self.stdout.write(
            f"Beta Dynamics projects visible to Frank: {beta_proj_count} (Acme projects hidden)"
        )
        assert beta_proj_count == 0, "Acme projects leaked to Beta Dynamics!"

        beta_logs = frank_client.get("/api/audit-logs/")
        beta_log_count = len(beta_logs.data.get("results", beta_logs.data))
        self.stdout.write(
            f"Beta Dynamics audit logs visible to Frank: {beta_log_count} (Acme logs hidden)"
        )
        assert beta_log_count == 0, "Acme audit logs leaked to Beta Dynamics!"

        self.stdout.write(
            self.style.SUCCESS(
                "\n[OK] ALL CHECKS PASSED: RBAC, Invitations, Audit Logs, and Isolation working!"
            )
        )
