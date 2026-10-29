from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.projects.models import Project
from apps.subscriptions.models import Plan, Subscription
from apps.tenants.context import get_tenant_alias
from apps.tenants.models import Membership, Tenant

POSTGRES_TENANT_ALIASES = {"tenant_a", "tenant_b"}
POSTGRES_CONFIGURED = settings.DATABASES["default"][
    "ENGINE"
] == "django.db.backends.postgresql" and POSTGRES_TENANT_ALIASES.issubset(settings.DATABASES)


@skipUnless(
    POSTGRES_CONFIGURED,
    "Requires PostgreSQL default, tenant_a, and tenant_b database aliases.",
)
@override_settings(
    ALLOW_DEFAULT_TENANT=False,
    ALLOWED_HOSTS=["tenant-a.test", "tenant-b.test", "unknown.test"],
)
class PostgreSQLTenantIsolationTests(TransactionTestCase):
    databases = {"default", "tenant_a", "tenant_b"}

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="shared-consultant",
            password="test-password",
        )
        self.tenant_a = Tenant.objects.using("default").create(
            name="Tenant A",
            slug="tenant-a",
            domain="tenant-a.test",
            database_alias="tenant_a",
        )
        self.tenant_b = Tenant.objects.using("default").create(
            name="Tenant B",
            slug="tenant-b",
            domain="tenant-b.test",
            database_alias="tenant_b",
        )
        Membership.objects.using("default").bulk_create(
            [
                Membership(user=self.user, tenant=self.tenant_a),
                Membership(user=self.user, tenant=self.tenant_b),
            ]
        )
        plan, _ = Plan.objects.using("default").get_or_create(
            code="free",
            defaults={
                "name": "Free",
                "price": "0.00",
                "currency": "USD",
                "billing_interval": "monthly",
                "is_active": True,
                "user_limit": 5,
                "usage_limits": {"projects": 3},
            },
        )
        for tenant in (self.tenant_a, self.tenant_b):
            Subscription.objects.using("default").create(
                tenant=tenant,
                plan=plan,
                status=Subscription.Status.ACTIVE,
                starts_at=timezone.now() - timedelta(minutes=1),
            )
        self.project_a = Project.objects.using("tenant_a").create(
            name="Tenant A secret",
            owner=self.user.pk,
        )
        self.project_b = Project.objects.using("tenant_b").create(
            pk=self.project_a.pk + 1000,
            name="Tenant B secret",
            owner=self.user.pk,
        )
        self.access_token = str(RefreshToken.for_user(self.user).access_token)

    def client_for(self, host):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access_token}")
        client.defaults["HTTP_HOST"] = host
        return client

    def test_each_tenant_reads_only_its_own_database(self):
        response_a = self.client_for("tenant-a.test").get("/api/projects/")
        response_b = self.client_for("tenant-b.test").get("/api/projects/")

        self.assertEqual(
            [item["name"] for item in response_a.data["results"]],
            ["Tenant A secret"],
        )
        self.assertEqual(
            [item["name"] for item in response_b.data["results"]],
            ["Tenant B secret"],
        )

    def test_cross_tenant_object_id_cannot_be_read_modified_or_deleted(self):
        client_a = self.client_for("tenant-a.test")
        object_url = f"/api/projects/{self.project_b.pk}/"

        read_response = client_a.get(object_url)
        update_response = client_a.patch(object_url, {"name": "stolen"}, format="json")
        delete_response = client_a.delete(object_url)

        self.assertEqual(read_response.status_code, 404)
        self.assertEqual(update_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)
        self.assertEqual(
            Project.objects.using("tenant_b").get(pk=self.project_b.pk).name,
            "Tenant B secret",
        )

    def test_tenant_write_uses_selected_database(self):
        response = self.client_for("tenant-a.test").post(
            "/api/projects/",
            {"name": "Created in A", "description": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Project.objects.using("tenant_a").filter(name="Created in A").exists())
        self.assertFalse(Project.objects.using("tenant_b").filter(name="Created in A").exists())

    def test_concurrent_project_creation_cannot_exceed_plan_limit(self):
        self.project_a.delete(using="tenant_a")
        plan, _ = Plan.objects.using("default").get_or_create(
            code="free",
            defaults={
                "name": "Free",
                "price": "0.00",
                "currency": "USD",
                "billing_interval": "monthly",
                "is_active": True,
                "user_limit": 5,
                "usage_limits": {"projects": 3},
            },
        )
        plan.usage_limits = {"projects": 1}
        plan.save(using="default", update_fields=["usage_limits"])
        barrier = Barrier(2)

        def create_project(name):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return (
                    self.client_for("tenant-a.test")
                    .post(
                        "/api/projects/",
                        {"name": name, "description": ""},
                        format="json",
                    )
                    .status_code
                )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create_project, ["Concurrent A", "Concurrent B"]))

        self.assertCountEqual(results, [201, 403])
        self.assertEqual(Project.objects.using("tenant_a").count(), 1)

    def test_unknown_tenant_host_is_rejected(self):
        response = self.client_for("unknown.test").get("/api/projects/")

        self.assertEqual(response.status_code, 404)

    def test_inactive_tenant_host_is_rejected(self):
        Tenant.objects.using("default").filter(pk=self.tenant_a.pk).update(is_active=False)

        response = self.client_for("tenant-a.test").get("/api/projects/")

        self.assertEqual(response.status_code, 404)

    def test_user_without_membership_cannot_select_tenant(self):
        outsider = get_user_model().objects.create_user(
            username="outside-consultant",
            password="test-password",
        )
        access_token = str(RefreshToken.for_user(outsider).access_token)
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
            HTTP_X_ORGANIZATION="tenant-a",
        )

        response = client.get("/api/projects/", HTTP_HOST="tenant-a.test")

        self.assertEqual(response.status_code, 403)

    def test_unconfigured_tenant_database_alias_fails_without_fallback(self):
        self.tenant_a.database_alias = "missing_tenant_database"
        self.tenant_a.save(using="default", update_fields=["database_alias"])

        with self.assertRaisesMessage(
            RuntimeError,
            "Tenant database alias 'missing_tenant_database' is not configured",
        ):
            self.client_for("tenant-a.test").get("/api/projects/")

    def test_unavailable_tenant_database_does_not_fall_back(self):
        connections["tenant_a"].close()

        with patch.object(
            connections["tenant_a"],
            "get_new_connection",
            side_effect=OperationalError("simulated tenant database outage"),
        ):
            with self.assertRaises(OperationalError):
                self.client_for("tenant-a.test").get("/api/projects/")

        self.assertTrue(Project.objects.using("tenant_b").filter(pk=self.project_b.pk).exists())

    def test_concurrent_tenant_requests_do_not_share_context_or_data(self):
        def request_projects(host):
            close_old_connections()
            try:
                response = self.client_for(host).get("/api/projects/")
                return (
                    response.status_code,
                    [item["name"] for item in response.data["results"]],
                    get_tenant_alias(),
                )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(request_projects, "tenant-a.test")
            future_b = executor.submit(request_projects, "tenant-b.test")
            result_a = future_a.result()
            result_b = future_b.result()

        self.assertEqual(result_a, (200, ["Tenant A secret"], None))
        self.assertEqual(result_b, (200, ["Tenant B secret"], None))
        self.assertIsNone(get_tenant_alias())
