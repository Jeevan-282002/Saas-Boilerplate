from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.routers import DefaultRouter

from apps.projects.views import ProjectViewSet
from apps.subscriptions.views import (
    CancelSubscriptionView,
    CurrentSubscriptionView,
    PlanListView,
    SubscriptionUsageView,
)
from apps.tenants.views import (
    AuditLogListView,
    InvitationAcceptView,
    InvitationListCreateView,
    InvitationRevokeView,
    MemberDetailView,
    MemberListView,
    OrganizationListView,
    ThrottledTokenObtainPairView,
    ThrottledTokenRefreshView,
    health,
)

router = DefaultRouter()
router.register("projects", ProjectViewSet, basename="project")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/organizations/", OrganizationListView.as_view(), name="organizations"),
    path("api/members/", MemberListView.as_view(), name="member-list"),
    path("api/members/<int:pk>/", MemberDetailView.as_view(), name="member-detail"),
    path("api/invitations/", InvitationListCreateView.as_view(), name="invitation-list"),
    path(
        "api/invitations/<int:pk>/revoke/",
        InvitationRevokeView.as_view(),
        name="invitation-revoke",
    ),
    path("api/invitations/accept/", InvitationAcceptView.as_view(), name="invitation-accept"),
    path("api/audit-logs/", AuditLogListView.as_view(), name="audit-log-list"),
    path("api/plans/", PlanListView.as_view(), name="plan-list"),
    path("api/subscription/", CurrentSubscriptionView.as_view(), name="current-subscription"),
    path("api/subscription/usage/", SubscriptionUsageView.as_view(), name="subscription-usage"),
    path(
        "api/subscription/cancel/",
        CancelSubscriptionView.as_view(),
        name="subscription-cancel",
    ),
    path("api/auth/token/", ThrottledTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", ThrottledTokenRefreshView.as_view(), name="token_refresh"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/", include(router.urls)),
]
