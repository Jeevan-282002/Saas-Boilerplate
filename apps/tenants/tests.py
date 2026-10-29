import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.db import OperationalError, connections
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.tenants.db_router import TenantDatabaseRouter


class TenantDatabaseRouterTests(SimpleTestCase):
    @override_settings(TENANT_DATABASE_APPS={"projects"}, DEFAULT_TENANT_ALIAS="default")
    def test_routes_tenant_models_to_context_alias(self):
        from apps.tenants.context import clear_tenant_alias, set_tenant_alias

        token = set_tenant_alias("tenant_a")
        try:
            router = TenantDatabaseRouter()
            project_model = SimpleNamespace(_meta=SimpleNamespace(app_label="projects"))
            self.assertEqual(router.db_for_read(project_model), "tenant_a")
        finally:
            clear_tenant_alias(token)

    @override_settings(TENANT_DATABASE_APPS={"projects"})
    def test_central_models_use_default_router_behavior(self):
        router = TenantDatabaseRouter()
        model = SimpleNamespace(_meta=SimpleNamespace(app_label="tenants"))
        self.assertIsNone(router.db_for_read(model))


class SecuritySettingsTests(SimpleTestCase):
    def run_settings_import(
        self,
        *,
        secret_key=None,
        debug=None,
        allowed_hosts="localhost",
        additional_env=None,
        code="import config.settings",
    ):
        env = os.environ.copy()
        for name in (
            "DJANGO_SECRET_KEY",
            "DJANGO_DEBUG",
            "DJANGO_ALLOWED_HOSTS",
            "DJANGO_LOAD_DOTENV",
            "DJANGO_ALLOW_DEFAULT_TENANT",
            "DJANGO_SECURE_SSL_REDIRECT",
            "DJANGO_SECURE_HSTS_SECONDS",
            "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
            "DJANGO_SECURE_HSTS_PRELOAD",
            "DJANGO_TRUST_X_FORWARDED_PROTO",
        ):
            env.pop(name, None)
        if secret_key is not None:
            env["DJANGO_SECRET_KEY"] = secret_key
        if debug is not None:
            env["DJANGO_DEBUG"] = debug
        if allowed_hosts is not None:
            env["DJANGO_ALLOWED_HOSTS"] = allowed_hosts
        if additional_env:
            env.update(additional_env)
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_missing_secret_key_fails_closed(self):
        result = self.run_settings_import()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY must be set", result.stderr)

    def test_debug_defaults_to_disabled(self):
        result = self.run_settings_import(
            secret_key="x" * 50,
            code="import config.settings; print(config.settings.DEBUG)",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False")

    def test_short_secret_key_is_rejected_outside_debug_mode(self):
        result = self.run_settings_import(
            secret_key="short-secret",
            debug="false",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("at least 50 characters", result.stderr)

    def test_wildcard_hosts_are_rejected_outside_debug_mode(self):
        result = self.run_settings_import(
            secret_key="x" * 50,
            debug="false",
            allowed_hosts="*",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit, non-wildcard", result.stderr)

    def test_subdomain_wildcard_hosts_are_rejected_outside_debug_mode(self):
        result = self.run_settings_import(
            secret_key="x" * 50,
            debug="false",
            allowed_hosts=".example.test",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit, non-wildcard", result.stderr)

    def test_hosts_must_be_explicit_outside_debug_mode(self):
        result = self.run_settings_import(
            secret_key="x" * 50,
            debug="false",
            allowed_hosts=None,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit, non-wildcard", result.stderr)

    def test_example_secret_key_is_rejected_even_in_debug_mode(self):
        result = self.run_settings_import(
            secret_key="replace-with-a-long-random-secret",
            debug="true",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Replace the example or development", result.stderr)

    def test_https_settings_are_disabled_in_debug_mode(self):
        result = self.run_settings_import(
            secret_key="local-test-secret",
            debug="true",
            additional_env={
                "DJANGO_SECURE_SSL_REDIRECT": "true",
                "DJANGO_SECURE_HSTS_SECONDS": "31536000",
                "DJANGO_TRUST_X_FORWARDED_PROTO": "true",
            },
            code=(
                "import config.settings as s; "
                "print((s.SECURE_SSL_REDIRECT, s.SECURE_HSTS_SECONDS, "
                "getattr(s, 'SECURE_PROXY_SSL_HEADER', None)))"
            ),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "(False, 0, None)")

    def test_production_https_settings_are_explicitly_configurable(self):
        result = self.run_settings_import(
            secret_key="x" * 50,
            debug="false",
            additional_env={
                "DJANGO_SECURE_SSL_REDIRECT": "true",
                "DJANGO_SECURE_HSTS_SECONDS": "31536000",
                "DJANGO_TRUST_X_FORWARDED_PROTO": "true",
            },
            code=(
                "import config.settings as s; "
                "print((s.SECURE_SSL_REDIRECT, s.SECURE_HSTS_SECONDS, "
                "s.SECURE_HSTS_INCLUDE_SUBDOMAINS, s.SECURE_HSTS_PRELOAD, "
                "s.SECURE_PROXY_SSL_HEADER))"
            ),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "(True, 31536000, False, False, ('HTTP_X_FORWARDED_PROTO', 'https'))",
        )

    def test_production_does_not_assume_tls_or_trust_proxy_headers_by_default(self):
        result = self.run_settings_import(
            secret_key="x" * 50,
            debug="false",
            code=(
                "import config.settings as s; "
                "print((s.SECURE_SSL_REDIRECT, s.SECURE_HSTS_SECONDS, "
                "getattr(s, 'SECURE_PROXY_SSL_HEADER', None)))"
            ),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "(False, 0, None)")

    def test_allow_default_tenant_is_rejected_outside_debug_mode(self):
        result = self.run_settings_import(
            secret_key="x" * 50,
            debug="false",
            additional_env={"DJANGO_ALLOW_DEFAULT_TENANT": "true"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "DJANGO_ALLOW_DEFAULT_TENANT must be disabled outside debug mode",
            result.stderr,
        )


class HealthCheckTests(TestCase):
    def test_health_check_returns_ok_when_database_is_healthy(self):
        client = APIClient()
        response = client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_check_returns_503_when_database_fails(self):
        client = APIClient()
        with patch.object(
            connections["default"],
            "cursor",
            side_effect=OperationalError("DB down"),
        ):
            response = client.get("/api/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unhealthy", "database": "unavailable"})
