from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsProjectOwnerOrHasChangePermission(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return request.user.has_perm("projects.view_project") or obj.owner_id == request.user.id
        return request.user.has_perm("projects.change_project") or obj.owner_id == request.user.id

