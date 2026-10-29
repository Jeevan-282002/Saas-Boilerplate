from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class Plan(models.Model):
    class BillingInterval(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    currency = models.CharField(max_length=3, default="USD")
    billing_interval = models.CharField(
        max_length=16,
        choices=BillingInterval.choices,
        default=BillingInterval.MONTHLY,
    )
    user_limit = models.PositiveIntegerField(null=True, blank=True)
    usage_limits = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price", "name"]

    def clean(self):
        super().clean()
        if not isinstance(self.usage_limits, dict):
            raise ValidationError({"usage_limits": "Usage limits must be a JSON object."})
        invalid = [
            key
            for key, limit in self.usage_limits.items()
            if not isinstance(key, str)
            or (
                limit is not None
                and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0)
            )
        ]
        if invalid:
            raise ValidationError(
                {"usage_limits": "Each usage limit must be a non-negative integer or null."}
            )

    def __str__(self):
        return self.name


class Subscription(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        TRIALING = "trialing", "Trialing"
        PAST_DUE = "past_due", "Past due"
        CANCELED = "canceled", "Canceled"
        EXPIRED = "expired", "Expired"

    tenant = models.OneToOneField(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )
    status = models.CharField(max_length=16, choices=Status.choices)
    starts_at = models.DateTimeField()
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    canceled_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    provider = models.CharField(max_length=32, blank=True)
    provider_subscription_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tenant.slug}: {self.plan.code} ({self.status})"
