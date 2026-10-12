from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

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
