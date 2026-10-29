from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.tenants.models import Membership, Tenant


class IsProjectOwnerOrHasChangePermission(BasePermission):
    message = "Select an active, fully provisioned organization."

    def has_permission(self, request, view):
        tenant = getattr(request, "tenant", None)
        if not (
            tenant
            and tenant.is_active
            and tenant.provisioning_status == Tenant.ProvisioningStatus.READY
        ):
            return False

        membership = (
            Membership.objects.using("default")
            .filter(user_id=request.user.pk, tenant_id=tenant.pk)
            .first()
        )
        if membership:
            if membership.role == Membership.Role.VIEWER and request.method not in SAFE_METHODS:
                raise PermissionDenied("Viewers have read-only access.")
        return True

    def has_object_permission(self, request, view, obj):
        tenant = getattr(request, "tenant", None)
        is_org_admin = False
        membership = None
        if tenant:
            membership = (
                Membership.objects.using("default")
                .filter(user_id=request.user.pk, tenant_id=tenant.pk)
                .first()
            )
            if membership and membership.role in {Membership.Role.OWNER, Membership.Role.ADMIN}:
                is_org_admin = True

        if request.method in SAFE_METHODS:
            is_viewer = bool(membership and membership.role == Membership.Role.VIEWER)
            return (
                is_org_admin
                or is_viewer
                or request.user.has_perm("projects.view_project")
                or obj.owner == request.user.id
            )
        if request.method == "DELETE":
            return (
                is_org_admin
                or request.user.has_perm("projects.delete_project")
                or obj.owner == request.user.id
            )
        return (
            is_org_admin
            or request.user.has_perm("projects.change_project")
            or obj.owner == request.user.id
        )
