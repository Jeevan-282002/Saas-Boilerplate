from django.conf import settings
from django.http import Http404

from apps.tenants.context import clear_tenant_alias, set_tenant_alias
from apps.tenants.models import Tenant


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        central_path = (
            request.path.startswith("/admin/")
            or request.path.startswith("/api/auth/")
            or request.path.startswith("/api/organizations/")
            or request.path.startswith("/api/docs/")
            or request.path.startswith("/api/redoc/")
            or request.path == "/api/schema/"
            or request.path == "/api/health/"
        )
        tenant = None
        if central_path:
            alias = settings.DEFAULT_TENANT_ALIAS
        else:
            host = request.get_host().split(":", 1)[0].lower().rstrip(".")
            tenant = Tenant.objects.filter(domain=host, is_active=True).first()

            if tenant:
                alias = tenant.database_alias
            elif settings.ALLOW_DEFAULT_TENANT or request.headers.get("X-Organization"):
                alias = settings.DEFAULT_TENANT_ALIAS
            else:
                raise Http404("No active tenant is configured for this host")

        if alias not in settings.DATABASES:
            raise RuntimeError(f"Tenant database alias {alias!r} is not configured")

        request.tenant = tenant
        token = set_tenant_alias(alias)
        try:
            return self.get_response(request)
        finally:
            clear_tenant_alias(token)
