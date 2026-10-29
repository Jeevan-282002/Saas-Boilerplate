from rest_framework import serializers

from apps.subscriptions.models import Plan, Subscription


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = [
            "id",
            "code",
            "name",
            "description",
            "price",
            "currency",
            "billing_interval",
            "user_limit",
            "usage_limits",
        ]


class SubscriptionSerializer(serializers.ModelSerializer):
    organization = serializers.CharField(source="tenant.slug", read_only=True)
    plan = PlanSerializer(read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id",
            "organization",
            "plan",
            "status",
            "starts_at",
            "trial_ends_at",
            "current_period_start",
            "current_period_end",
            "cancel_at_period_end",
            "canceled_at",
            "ended_at",
        ]


class CancelSubscriptionSerializer(serializers.Serializer):
    immediately = serializers.BooleanField(required=False, default=False)


class UsageMetricSerializer(serializers.Serializer):
    used = serializers.IntegerField()
    limit = serializers.IntegerField(allow_null=True)
    remaining = serializers.IntegerField(allow_null=True)


class SubscriptionUsageSerializer(serializers.Serializer):
    users = UsageMetricSerializer()
    projects = UsageMetricSerializer()
