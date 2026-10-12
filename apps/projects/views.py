from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from apps.projects.models import Project
from apps.projects.permissions import IsProjectOwnerOrHasChangePermission
from apps.projects.serializers import ProjectSerializer


class ProjectViewSet(ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated, IsProjectOwnerOrHasChangePermission]
    filterset_fields = ["owner"]
    ordering_fields = ["created_at", "name"]

    def get_queryset(self):
        queryset = Project.objects.all()
        if self.request.user.has_perm("projects.view_project"):
            return queryset
        return queryset.filter(owner=self.request.user.pk)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user.pk)
