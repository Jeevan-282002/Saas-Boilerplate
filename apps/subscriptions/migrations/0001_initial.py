import decimal

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def create_default_plans(apps, schema_editor):
    Plan = apps.get_model("subscriptions", "Plan")
    plans = [
        {
            "code": "free",
            "name": "Free",
            "description": "For trying the product with a small team.",
            "price": "0.00",
            "user_limit": 5,
            "usage_limits": {"projects": 3},
        },
        {
            "code": "pro",
            "name": "Pro",
            "description": "For growing teams.",
            "price": "29.00",
            "user_limit": 25,
            "usage_limits": {"projects": 100},
        },
        {
            "code": "business",
            "name": "Business",
            "description": "For larger organizations.",
            "price": "99.00",
            "user_limit": None,
            "usage_limits": {"projects": None},
        },
    ]
    for plan in plans:
        Plan.objects.using(schema_editor.connection.alias).update_or_create(
            code=plan.pop("code"),
            defaults={
                **plan,
                "currency": "USD",
                "billing_interval": "monthly",
                "is_active": True,
            },
        )


def seed_existing_tenant_subscriptions(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    Plan = apps.get_model("subscriptions", "Plan")
    Subscription = apps.get_model("subscriptions", "Subscription")
    database = schema_editor.connection.alias
    free_plan = Plan.objects.using(database).get(code="free")
    tenant_fields = {field.name for field in Tenant._meta.get_fields()}
    tenants = Tenant.objects.using(database).all()
    if "is_active" in tenant_fields:
        tenants = tenants.filter(is_active=True)
    if "provisioning_status" in tenant_fields:
        tenants = tenants.filter(provisioning_status="ready")
    for tenant in tenants.iterator():
        Subscription.objects.using(database).get_or_create(
            tenant_id=tenant.pk,
            defaults={
                "plan_id": free_plan.pk,
                "status": "active",
                "starts_at": django.utils.timezone.now(),
            },
        )


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tenants", "0003_tenant_provisioning_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="Plan",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("code", models.SlugField(max_length=32, unique=True)),
                ("name", models.CharField(max_length=80)),
                ("description", models.TextField(blank=True)),
                (
                    "price",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=10,
                        validators=[django.core.validators.MinValueValidator(decimal.Decimal("0"))],
                    ),
                ),
                ("currency", models.CharField(default="USD", max_length=3)),
                (
                    "billing_interval",
                    models.CharField(
                        choices=[("monthly", "Monthly"), ("yearly", "Yearly")],
                        default="monthly",
                        max_length=16,
                    ),
                ),
                ("user_limit", models.PositiveIntegerField(blank=True, null=True)),
                ("usage_limits", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["price", "name"]},
        ),
        migrations.CreateModel(
            name="Subscription",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("trialing", "Trialing"),
                            ("past_due", "Past due"),
                            ("canceled", "Canceled"),
                            ("expired", "Expired"),
                        ],
                        max_length=16,
                    ),
                ),
                ("starts_at", models.DateTimeField()),
                ("trial_ends_at", models.DateTimeField(blank=True, null=True)),
                ("current_period_start", models.DateTimeField(blank=True, null=True)),
                ("current_period_end", models.DateTimeField(blank=True, null=True)),
                ("cancel_at_period_end", models.BooleanField(default=False)),
                ("canceled_at", models.DateTimeField(blank=True, null=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("provider", models.CharField(blank=True, max_length=32)),
                ("provider_subscription_id", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="subscriptions",
                        to="subscriptions.plan",
                    ),
                ),
                (
                    "tenant",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscription",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.RunPython(create_default_plans, migrations.RunPython.noop),
        migrations.RunPython(seed_existing_tenant_subscriptions, migrations.RunPython.noop),
    ]
