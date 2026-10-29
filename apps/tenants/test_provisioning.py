import unittest
import uuid
from unittest.mock import patch

import psycopg
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connections
from django.db.backends.base.base import NO_DB_ALIAS, BaseDatabaseWrapper
from django.db.utils import OperationalError as DjangoOperationalError
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.test.testcases import _DatabaseFailure
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.projects.models import Project
from apps.subscriptions.models import Plan
from apps.tenants.models import Membership, Tenant
from apps.tenants.provisioning import (
    provision_tenant_database,
    register_tenant_database,
)


@override_settings(TENANT_DOMAIN_SUFFIX="localhost")
class OrganizationSignupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="organization-owner",
            password="test-password",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch("apps.tenants.views.provision_tenant_database")
    def test_signup_provisions_tenant_and_assigns_creator_admin(self, provision):
        response = self.client.post(
            "/api/organizations/",
            {"name": "Acme Workspace"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Acme Workspace")
        self.assertEqual(response.data["slug"], "acme-workspace")
        tenant = Tenant.objects.get(slug="acme-workspace")
        membership = Membership.objects.get(tenant=tenant, user=self.user)
        self.assertEqual(membership.role, Membership.Role.ADMIN)
        self.assertTrue(tenant.is_active)
        self.assertEqual(tenant.provisioning_status, Tenant.ProvisioningStatus.READY)
        provision.assert_called_once_with(tenant)

        list_response = self.client.get("/api/organizations/")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["results"][0]["slug"], "acme-workspace")

    def test_signup_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(
            "/api/organizations/",
            {"name": "Acme Workspace"},
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertFalse(Tenant.objects.exists())

    @patch("apps.tenants.views.provision_tenant_database")
    def test_duplicate_slug_returns_conflict_without_second_provision(self, provision):
        self.client.post(
            "/api/organizations/",
            {"name": "Acme Workspace"},
            format="json",
        )

        response = self.client.post(
            "/api/organizations/",
            {"name": "Acme Workspace"},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Tenant.objects.filter(slug="acme-workspace").count(), 1)
        provision.assert_called_once()

    @patch("apps.tenants.views.provision_tenant_database")
    def test_failed_provisioning_can_be_retried_without_duplicate_records(self, provision):
        provision.side_effect = [RuntimeError("database password must not leak"), None]
        payload = {"name": "Retry Workspace"}

        first_response = self.client.post("/api/organizations/", payload, format="json")

        self.assertEqual(first_response.status_code, 503)
        self.assertNotIn(b"database password", first_response.content)
        tenant = Tenant.objects.get(slug="retry-workspace")
        self.assertFalse(tenant.is_active)
        self.assertEqual(tenant.provisioning_status, Tenant.ProvisioningStatus.PENDING)
        self.assertEqual(
            Membership.objects.filter(tenant=tenant, user=self.user).count(),
            1,
        )

        retry_response = self.client.post("/api/organizations/", payload, format="json")

        self.assertEqual(retry_response.status_code, 200)
        tenant.refresh_from_db()
        self.assertTrue(tenant.is_active)
        self.assertEqual(tenant.provisioning_status, Tenant.ProvisioningStatus.READY)
        self.assertEqual(
            Membership.objects.filter(tenant=tenant, user=self.user).count(),
            1,
        )
        self.assertEqual(provision.call_count, 2)

    @patch("apps.tenants.views.provision_tenant_database")
    def test_activation_failure_keeps_tenant_inactive_and_retryable(self, provision):
        with patch("apps.tenants.views.Plan.objects") as plans:
            plans.using.return_value.get.side_effect = Plan.DoesNotExist("Free plan missing")
            response = self.client.post(
                "/api/organizations/",
                {"name": "Activation Retry Workspace"},
                format="json",
            )

        self.assertEqual(response.status_code, 503)
        tenant = Tenant.objects.get(slug="activation-retry-workspace")
        self.assertFalse(tenant.is_active)
        self.assertEqual(tenant.provisioning_status, Tenant.ProvisioningStatus.PENDING)
        provision.assert_called_once_with(tenant)

    @patch("apps.tenants.views.provision_tenant_database")
    def test_database_credentials_are_rejected_from_signup_payload(self, provision):
        response = self.client.post(
            "/api/organizations/",
            {"name": "Unsafe Workspace", "database_url": "postgresql://secret"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Tenant.objects.exists())
        provision.assert_not_called()


@override_settings(
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "central",
            "USER": "runtime-user",
            "PASSWORD": "runtime-password",
            "HOST": "postgres",
            "PORT": "5432",
            "OPTIONS": {},
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    }
)
class RuntimeDatabaseRegistrationTests(SimpleTestCase):
    def test_generated_tenant_database_alias_is_registered_from_default_credentials(self):
        alias = "tenant_0123456789abcdef0123456789abcdef"
        try:
            register_tenant_database(alias)
            self.assertEqual(connections.databases[alias]["NAME"], alias)
            self.assertEqual(connections.databases[alias]["USER"], "runtime-user")
            self.assertEqual(
                connections.databases[alias]["PASSWORD"],
                "runtime-password",
            )
        finally:
            connections.databases.pop(alias, None)

    def test_invalid_dynamic_alias_is_rejected(self):
        with self.assertRaisesMessage(
            RuntimeError,
            "Tenant database alias is not a valid generated alias.",
        ):
            register_tenant_database("tenant_unsafe")

    def test_generated_alias_with_unexpected_database_config_is_rejected(self):
        alias = "tenant_0123456789abcdef0123456789abcdef"
        connections.databases[alias] = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "another_database",
        }
        try:
            with self.assertRaisesMessage(
                RuntimeError,
                "Tenant database alias has an unexpected database configuration.",
            ):
                register_tenant_database(alias)
        finally:
            connections.databases.pop(alias, None)

    def test_existing_preconfigured_alias_is_preserved(self):
        alias = "tenant_preconfigured"
        connections.databases[alias] = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "preconfigured_database",
        }
        try:
            self.assertEqual(register_tenant_database(alias), alias)
        finally:
            connections.databases.pop(alias, None)


@override_settings(
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "central",
            "USER": "runtime-user",
            "PASSWORD": "runtime-password",
            "HOST": "postgres",
            "PORT": "5432",
            "OPTIONS": {},
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    },
    TENANT_DATABASE_ADMIN_URL="postgresql://provisioner:secret@postgres/postgres",
)
class TenantDatabaseProvisioningServiceTests(SimpleTestCase):
    alias = "tenant_abcdef0123456789abcdef0123456789"

    def tearDown(self):
        connections.databases.pop(self.alias, None)

    @patch("apps.tenants.provisioning.call_command")
    @patch("apps.tenants.provisioning.psycopg.connect")
    def test_creates_database_with_quoted_identifiers_and_runs_migrations(
        self,
        connect,
        call_command_mock,
    ):
        admin_connection = connect.return_value.__enter__.return_value
        admin_connection.execute.return_value.fetchone.return_value = None
        tenant = Tenant(database_alias=self.alias)

        provision_tenant_database(tenant)

        create_database_statement = next(
            call.args[0]
            for call in admin_connection.execute.call_args_list
            if isinstance(call.args[0], psycopg.sql.Composed)
        )
        identifiers = [
            item._obj[0]
            for item in create_database_statement._obj
            if isinstance(item, psycopg.sql.Identifier)
        ]
        self.assertEqual(identifiers, [self.alias, "runtime-user"])
        call_command_mock.assert_called_once_with(
            "migrate",
            database=self.alias,
            interactive=False,
            verbosity=0,
        )

    @patch("apps.tenants.provisioning.call_command")
    @patch("apps.tenants.provisioning.psycopg.connect")
    def test_reuses_existing_database_only_when_owner_matches(
        self,
        connect,
        call_command_mock,
    ):
        admin_connection = connect.return_value.__enter__.return_value
        admin_connection.execute.return_value.fetchone.return_value = ("runtime-user",)

        provision_tenant_database(Tenant(database_alias=self.alias))

        self.assertFalse(
            any(
                isinstance(call.args[0], psycopg.sql.Composed)
                for call in admin_connection.execute.call_args_list
            )
        )
        call_command_mock.assert_called_once()

    @patch("apps.tenants.provisioning.psycopg.connect")
    def test_refuses_to_reuse_database_with_different_owner(self, connect):
        admin_connection = connect.return_value.__enter__.return_value
        admin_connection.execute.return_value.fetchone.return_value = ("unexpected-owner",)

        with self.assertRaisesMessage(
            RuntimeError,
            "Existing tenant database has an unexpected owner.",
        ):
            provision_tenant_database(Tenant(database_alias=self.alias))

    def test_connection_errors_with_permanent_sqlstate_are_not_transient(self):
        from apps.tenants.provisioning import _is_transient_connection_error

        error = psycopg.errors.InvalidPassword("invalid credentials")
        self.assertFalse(_is_transient_connection_error(error))
        wrapped_error = DjangoOperationalError("database connection failed")
        wrapped_error.__cause__ = error
        self.assertFalse(_is_transient_connection_error(wrapped_error))

    @patch("apps.tenants.provisioning.time.sleep")
    @patch("apps.tenants.provisioning.call_command")
    @patch("apps.tenants.provisioning.psycopg.connect")
    def test_retries_a_transient_admin_connection_failure(
        self,
        connect,
        call_command_mock,
        sleep,
    ):
        connection_context = connect.return_value
        admin_connection = connection_context.__enter__.return_value
        admin_connection.execute.return_value.fetchone.return_value = None
        connect.side_effect = [
            psycopg.OperationalError("temporary connection failure"),
            connection_context,
        ]

        provision_tenant_database(Tenant(database_alias=self.alias))

        self.assertEqual(connect.call_count, 2)
        sleep.assert_called_once_with(0.1)
        call_command_mock.assert_called_once_with(
            "migrate",
            database=self.alias,
            interactive=False,
            verbosity=0,
        )

    @patch("apps.tenants.provisioning.time.sleep")
    @patch("apps.tenants.provisioning.call_command")
    @patch("apps.tenants.provisioning.psycopg.connect")
    def test_migration_failure_preserves_database_for_idempotent_retry(
        self,
        connect,
        call_command_mock,
        sleep,
    ):
        admin_connection = connect.return_value.__enter__.return_value
        admin_connection.execute.return_value.fetchone.side_effect = [
            None,
            ("runtime-user",),
        ]
        call_command_mock.side_effect = [RuntimeError("migration failed"), None]
        tenant = Tenant(database_alias=self.alias)

        with self.assertRaisesMessage(RuntimeError, "migration failed"):
            provision_tenant_database(tenant)
        provision_tenant_database(tenant)

        self.assertEqual(connect.call_count, 2)
        self.assertEqual(call_command_mock.call_count, 2)
        self.assertFalse(sleep.called)
        create_database_calls = [
            call
            for call in admin_connection.execute.call_args_list
            if isinstance(call.args[0], psycopg.sql.Composed)
        ]
        self.assertEqual(len(create_database_calls), 1)

    @patch("apps.tenants.provisioning.time.sleep")
    @patch("apps.tenants.provisioning.call_command")
    @patch("apps.tenants.provisioning.psycopg.connect")
    def test_permanent_database_creation_failure_is_not_retried(
        self,
        connect,
        call_command_mock,
        sleep,
    ):
        admin_connection = connect.return_value.__enter__.return_value
        admin_connection.execute.return_value.fetchone.return_value = None

        def fail_database_creation(statement, *args):
            if isinstance(statement, psycopg.sql.Composed):
                raise psycopg.errors.InsufficientPrivilege("database creation denied")
            return admin_connection.execute.return_value

        admin_connection.execute.side_effect = fail_database_creation

        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            provision_tenant_database(Tenant(database_alias=self.alias))

        connect.assert_called_once()
        call_command_mock.assert_not_called()
        sleep.assert_not_called()

    @patch("apps.tenants.provisioning.call_command")
    @patch("apps.tenants.provisioning.psycopg.connect")
    def test_repeated_provisioning_reuses_owned_database(self, connect, call_command_mock):
        admin_connection = connect.return_value.__enter__.return_value
        admin_connection.execute.return_value.fetchone.side_effect = [
            None,
            ("runtime-user",),
        ]
        tenant = Tenant(database_alias=self.alias)

        provision_tenant_database(tenant)
        provision_tenant_database(tenant)

        create_database_calls = [
            call
            for call in admin_connection.execute.call_args_list
            if isinstance(call.args[0], psycopg.sql.Composed)
        ]
        self.assertEqual(len(create_database_calls), 1)
        self.assertEqual(call_command_mock.call_count, 2)

    @patch("apps.tenants.provisioning.time.sleep")
    @patch("apps.tenants.provisioning.psycopg.connect")
    def test_transient_connection_retries_stop_after_the_configured_limit(
        self,
        connect,
        sleep,
    ):
        connect.side_effect = psycopg.OperationalError("temporary connection failure")

        with self.assertRaises(psycopg.OperationalError):
            provision_tenant_database(Tenant(database_alias=self.alias))

        self.assertEqual(connect.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.1, 0.2])


@pytest.mark.django_db(transaction=True)
@unittest.skipUnless(
    settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"
    and settings.TENANT_DATABASE_ADMIN_URL,
    "Requires a PostgreSQL provisioning role and disposable test database.",
)
class PostgreSQLOrganizationProvisioningTests(TransactionTestCase):
    databases = {"default", "tenant_a", "tenant_b"}

    @classmethod
    def ensure_connection_patch_method(cls):
        real_ensure_connection = BaseDatabaseWrapper.ensure_connection

        def patched_ensure_connection(self, *args, **kwargs):
            if (
                self.connection is None
                and self.alias not in cls.databases
                and not self.alias.startswith("tenant_")
                and self.alias != NO_DB_ALIAS
                and self.alias in connections
            ):
                message = cls._disallowed_database_msg % {
                    "test": f"{cls.__module__}.{cls.__qualname__}",
                    "alias": self.alias,
                    "operation": "threaded connections",
                }
                return _DatabaseFailure(self.ensure_connection, message)()

            real_ensure_connection(self, *args, **kwargs)

        return patched_ensure_connection

    def setUp(self):
        Plan.objects.using("default").get_or_create(
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

    def test_partial_database_creation_can_be_recovered_without_dropping_database(self):
        alias = f"tenant_{uuid.uuid4().hex}"
        owner = settings.DATABASES["default"]["USER"]
        with psycopg.connect(settings.TENANT_DATABASE_ADMIN_URL) as admin_connection:
            existing_database = admin_connection.execute(
                "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
                (alias,),
            ).fetchone()
        self.assertIsNone(existing_database)

        tenant = Tenant(database_alias=alias)
        try:
            with patch(
                "apps.tenants.provisioning.call_command",
                side_effect=RuntimeError("simulated migration failure"),
            ):
                with self.assertRaisesMessage(RuntimeError, "simulated migration failure"):
                    provision_tenant_database(tenant)

            with psycopg.connect(settings.TENANT_DATABASE_ADMIN_URL) as admin_connection:
                existing_owner = admin_connection.execute(
                    """
                    SELECT pg_catalog.pg_get_userbyid(datdba)
                    FROM pg_catalog.pg_database
                    WHERE datname = %s
                    """,
                    (alias,),
                ).fetchone()
            self.assertEqual(existing_owner, (owner,))

            provision_tenant_database(tenant)
            self.assertFalse(Project.objects.using(alias).exists())
        finally:
            if alias in connections.databases:
                connections[alias].close()
            try:
                with psycopg.connect(
                    settings.TENANT_DATABASE_ADMIN_URL,
                    autocommit=True,
                ) as admin_connection:
                    existing_owner = admin_connection.execute(
                        """
                        SELECT pg_catalog.pg_get_userbyid(datdba)
                        FROM pg_catalog.pg_database
                        WHERE datname = %s
                        """,
                        (alias,),
                    ).fetchone()
                    if existing_owner == (owner,):
                        admin_connection.execute(
                            psycopg.sql.SQL("DROP DATABASE {}").format(
                                psycopg.sql.Identifier(alias)
                            )
                        )
            finally:
                connections.databases.pop(alias, None)

    def test_signup_creates_and_routes_to_a_migrated_tenant_database(self):
        user = get_user_model().objects.create_user(
            username="provisioned-organization-owner",
            password="test-password",
        )
        client = APIClient()
        token = str(RefreshToken.for_user(user).access_token)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        tenant = None
        alias = None
        try:
            response = client.post(
                "/api/organizations/",
                {"name": "Provisioned Workspace"},
                format="json",
            )

            self.assertEqual(response.status_code, 201, response.data)
            tenant = Tenant.objects.get(slug="provisioned-workspace")
            alias = tenant.database_alias
            self.assertRegex(alias, r"\Atenant_[0-9a-f]{32}\Z")
            self.assertTrue(tenant.is_active)
            self.assertEqual(
                Membership.objects.get(user=user, tenant=tenant).role,
                Membership.Role.ADMIN,
            )
            self.assertFalse(Project.objects.using(alias).exists())

            project_response = client.post(
                "/api/projects/",
                {"name": "Created in provisioned tenant", "description": ""},
                format="json",
                HTTP_X_ORGANIZATION=tenant.slug,
            )

            self.assertEqual(project_response.status_code, 201, project_response.data)
            self.assertTrue(
                Project.objects.using(alias).filter(name="Created in provisioned tenant").exists()
            )
            self.assertFalse(
                Project.objects.using("tenant_a")
                .filter(name="Created in provisioned tenant")
                .exists()
            )
        finally:
            if alias is None:
                tenant = Tenant.objects.filter(slug="provisioned-workspace").first()
                if tenant:
                    alias = tenant.database_alias
            if alias and alias in connections.databases:
                connections[alias].close()
                with psycopg.connect(
                    settings.TENANT_DATABASE_ADMIN_URL,
                    autocommit=True,
                ) as admin_connection:
                    admin_connection.execute(
                        psycopg.sql.SQL("DROP DATABASE IF EXISTS {}").format(
                            psycopg.sql.Identifier(alias)
                        )
                    )
                connections.databases.pop(alias, None)
