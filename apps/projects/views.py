from django.conf import settings
from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from apps.projects.models import Project
from apps.projects.permissions import IsProjectOwnerOrHasChangePermission
from apps.projects.serializers import ProjectSerializer
from apps.subscriptions.services import enforce_project_limit, get_current_subscription
from apps.tenants.audit import log_audit_event
from apps.tenants.models import Membership, Tenant
from apps.tenants.permissions import get_current_membership


class ProjectViewSet(ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated, IsProjectOwnerOrHasChangePermission]
    filterset_fields = ["owner"]
    ordering_fields = ["created_at", "name"]

    def get_queryset(self):
        queryset = Project.objects.all()
        tenant = getattr(self.request, "tenant", None)
        if tenant:
            membership = get_current_membership(self.request.user, tenant)
            if membership and membership.role in {
                Membership.Role.OWNER,
                Membership.Role.ADMIN,
                Membership.Role.VIEWER,
            }:
                return queryset
        if self.request.user.has_perm("projects.view_project"):
            return queryset
        return queryset.filter(owner=self.request.user.pk)

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if tenant is not None:
            tenant_db = tenant.database_alias or settings.DEFAULT_TENANT_ALIAS
            with transaction.atomic(using="default"), transaction.atomic(using=tenant_db):
                Tenant.objects.using("default").select_for_update().get(pk=tenant.pk)
                subscription = get_current_subscription(tenant, for_update=True)
                enforce_project_limit(subscription, tenant.database_alias)
                instance = serializer.save(owner=self.request.user.pk)
            log_audit_event(
                tenant=tenant,
                action="project.created",
                resource_type="project",
                resource_id=instance.pk,
                actor=self.request.user,
                metadata={"name": instance.name},
                request=self.request,
            )
            return
        serializer.save(owner=self.request.user.pk)

    def perform_update(self, serializer):
        instance = serializer.save()
        tenant = getattr(self.request, "tenant", None)
        if tenant is not None:
            log_audit_event(
                tenant=tenant,
                action="project.updated",
                resource_type="project",
                resource_id=instance.pk,
                actor=self.request.user,
                metadata={"name": instance.name},
                request=self.request,
            )

    def perform_destroy(self, instance):
        tenant = getattr(self.request, "tenant", None)
        project_id = instance.pk
        project_name = instance.name
        instance.delete()
        if tenant is not None:
            log_audit_event(
                tenant=tenant,
                action="project.deleted",
                resource_type="project",
                resource_id=project_id,
                actor=self.request.user,
                metadata={"name": project_name},
                request=self.request,
            )
