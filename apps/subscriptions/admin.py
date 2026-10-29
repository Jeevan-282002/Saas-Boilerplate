from django.contrib import admin

from apps.subscriptions.models import Plan, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "price", "currency", "billing_interval", "is_active"]
    list_filter = ["billing_interval", "is_active"]
    search_fields = ["name", "code"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["tenant", "plan", "status", "current_period_end", "cancel_at_period_end"]
    list_filter = ["status", "plan"]
    search_fields = ["tenant__name", "tenant__slug", "provider_subscription_id"]
