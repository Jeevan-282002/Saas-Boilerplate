from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.projects.models import Project
from apps.tenants.models import Membership, Tenant


@override_settings(
    ALLOW_DEFAULT_TENANT=True,
    ALLOWED_HOSTS=["testserver", "localhost", "company-a.example.com"],
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
