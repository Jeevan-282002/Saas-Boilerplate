from django.db import connections
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.tenants.context import set_tenant_alias
from apps.tenants.models import Membership, Tenant
from apps.tenants.provisioning import register_tenant_database


class TenantJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, token = result
        organization_slug = request.headers.get("X-Organization")
        tenant = getattr(request, "tenant", None)

        if organization_slug:
            tenant = (
                Tenant.objects.using("default")
                .filter(
                    slug=organization_slug,
                    is_active=True,
                    provisioning_status=Tenant.ProvisioningStatus.READY,
                )
                .first()
            )
            if tenant is None:
                raise PermissionDenied("The selected organization is unavailable.")
        elif tenant is None:
            return user, token

        if not Membership.objects.using("default").filter(user_id=user.pk, tenant=tenant).exists():
            raise PermissionDenied("You are not a member of the selected organization.")
        request.tenant = tenant
        register_tenant_database(tenant.database_alias)
        if tenant.database_alias not in connections.databases:
            raise RuntimeError("The selected tenant database is not configured.")

        set_tenant_alias(tenant.database_alias)
        return user, token
