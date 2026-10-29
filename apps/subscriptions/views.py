from django.db import connections
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.subscriptions.models import Plan
from apps.subscriptions.permissions import CurrentOrganizationAdmin, CurrentOrganizationMember
from apps.subscriptions.serializers import (
    CancelSubscriptionSerializer,
    PlanSerializer,
    SubscriptionSerializer,
    SubscriptionUsageSerializer,
)
from apps.subscriptions.services import (
    cancel_subscription,
    get_current_subscription,
    get_subscription_for_tenant,
    get_usage,
)


class PlanListView(ListAPIView):
    queryset = Plan.objects.filter(is_active=True)
    serializer_class = PlanSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "subscription_read"


class CurrentSubscriptionView(APIView):
    permission_classes = [IsAuthenticated, CurrentOrganizationMember]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "subscription_read"

    @extend_schema(responses=SubscriptionSerializer)
    def get(self, request):
        subscription = get_subscription_for_tenant(request.tenant)
        return Response(SubscriptionSerializer(subscription).data)


class SubscriptionUsageView(APIView):
    permission_classes = [IsAuthenticated, CurrentOrganizationMember]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "subscription_read"

    @extend_schema(responses=SubscriptionUsageSerializer)
    def get(self, request):
        subscription = get_current_subscription(request.tenant)
        if subscription.tenant.database_alias not in connections.databases:
            raise PermissionDenied("The selected tenant database is not configured.")
        return Response(get_usage(subscription, subscription.tenant.database_alias))


class CancelSubscriptionView(APIView):
    permission_classes = [IsAuthenticated, CurrentOrganizationAdmin]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "subscription_read"

    @extend_schema(
        request=CancelSubscriptionSerializer,
        responses=SubscriptionSerializer,
    )
    def post(self, request):
        serializer = CancelSubscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subscription = get_subscription_for_tenant(request.tenant)
        subscription = cancel_subscription(
            subscription,
            immediately=serializer.validated_data["immediately"],
        )
        return Response(
            SubscriptionSerializer(subscription).data,
            status=status.HTTP_200_OK,
        )
