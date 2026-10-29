from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.tenants.models import Membership, Tenant


def get_current_membership(user, tenant) -> Membership | None:
    if not user or not user.is_authenticated or not tenant:
        return None
    return Membership.objects.using("default").filter(user_id=user.pk, tenant_id=tenant.pk).first()


class HasOrganizationAccess(BasePermission):
    """Base check that an active, ready organization is selected and user is a member."""

    message = "Select an active, fully provisioned organization."

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

        membership = get_current_membership(request.user, tenant)
        if not membership:
            raise PermissionDenied("You are not a member of the selected organization.")

        request.membership = membership
        return True


class IsOrganizationOwner(HasOrganizationAccess):
    """Requires OWNER role within the current organization."""

    def has_permission(self, request, view):
        super().has_permission(request, view)
        if request.membership.role != Membership.Role.OWNER:
            raise PermissionDenied("Organization owner access is required.")
        return True


class IsOrganizationAdmin(HasOrganizationAccess):
    """Requires OWNER or ADMIN role within the current organization."""

    def has_permission(self, request, view):
        super().has_permission(request, view)
        if request.membership.role not in {Membership.Role.OWNER, Membership.Role.ADMIN}:
            raise PermissionDenied("Organization admin or owner access is required.")
        return True


class CanManageMembers(HasOrganizationAccess):
    """Only OWNER and ADMIN can manage members and invitations."""

    def has_permission(self, request, view):
        super().has_permission(request, view)
        if request.membership.role not in {Membership.Role.OWNER, Membership.Role.ADMIN}:
            raise PermissionDenied("Organization admin or owner access is required.")
        return True
