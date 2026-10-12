from django.conf import settings

from apps.tenants.context import get_tenant_alias


class TenantDatabaseRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label in settings.TENANT_DATABASE_APPS:
            return get_tenant_alias() or settings.DEFAULT_TENANT_ALIAS
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in settings.TENANT_DATABASE_APPS:
            return get_tenant_alias() or settings.DEFAULT_TENANT_ALIAS
        return None

    def allow_relation(self, obj1, obj2, **hints):
        tenant_apps = settings.TENANT_DATABASE_APPS
        obj1_is_tenant = obj1._meta.app_label in tenant_apps
        obj2_is_tenant = obj2._meta.app_label in tenant_apps
        if obj1_is_tenant or obj2_is_tenant:
            return obj1_is_tenant and obj2_is_tenant
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in settings.TENANT_DATABASE_APPS:
            return db != "default" or settings.ALLOW_DEFAULT_TENANT
        return db == "default"
