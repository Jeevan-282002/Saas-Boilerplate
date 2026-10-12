from django.db import connections
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.tenants.models import Membership
from apps.tenants.serializers import OrganizationMembershipSerializer


class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "token_obtain"


class ThrottledTokenRefreshView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "token_refresh"


@require_GET
def health(request):
    connections["default"].ensure_connection()
    return JsonResponse({"status": "ok"})


class OrganizationListView(ListAPIView):
    serializer_class = OrganizationMembershipSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            Membership.objects.using("default")
            .filter(user_id=self.request.user.pk, tenant__is_active=True)
            .select_related("tenant")
        )
