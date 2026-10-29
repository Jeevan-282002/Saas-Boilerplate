from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.tenants.models import Membership, Tenant


class CurrentOrganizationMember(BasePermission):
    def has_permission(self, request, view):
        tenant = getattr(request, "tenant", None)
        if (
            tenant is None
            or not tenant.is_active
            or tenant.provisioning_status != Tenant.ProvisioningStatus.READY
        ):
            raise PermissionDenied(
                "Select an active, fully provisioned organization with X-Organization."
            )
        if (
            not Membership.objects.using("default")
            .filter(
                user_id=request.user.pk,
                tenant_id=tenant.pk,
            )
            .exists()
        ):
            raise PermissionDenied("You are not a member of the selected organization.")
        return True


class CurrentOrganizationAdmin(CurrentOrganizationMember):
    def has_permission(self, request, view):
        super().has_permission(request, view)
        if (
            not Membership.objects.using("default")
            .filter(
                user_id=request.user.pk,
                tenant_id=request.tenant.pk,
                role__in=[Membership.Role.OWNER, Membership.Role.ADMIN],
            )
            .exists()
        ):
            raise PermissionDenied("Organization admin access is required.")
        return True
