from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.projects.models import Project
from apps.subscriptions.models import Plan, Subscription
from apps.subscriptions.providers import VerifiedSubscriptionEvent
from apps.subscriptions.services import (
    apply_verified_provider_event,
    cancel_subscription,
    expire_due_subscriptions,
)
from apps.subscriptions.tasks import expire_subscriptions
from apps.tenants.models import Membership, Tenant


@override_settings(ALLOW_DEFAULT_TENANT=True, ALLOWED_HOSTS=["testserver", "workspace.test"])
class SubscriptionApiTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="subscription-owner",
            password="test-password",
        )
        self.other_owner = get_user_model().objects.create_user(
            username="other-subscription-owner",
            password="test-password",
        )
        self.tenant = Tenant.objects.create(
            name="Workspace",
            slug="workspace",
            domain="workspace.test",
            database_alias="default",
        )
        self.other_tenant = Tenant.objects.create(
            name="Other workspace",
            slug="other-workspace",
            domain="other-workspace.test",
            database_alias="default",
        )
        Membership.objects.create(
            user=self.owner,
            tenant=self.tenant,
            role=Membership.Role.ADMIN,
        )
        Membership.objects.create(
            user=self.other_owner,
            tenant=self.other_tenant,
            role=Membership.Role.ADMIN,
        )
        self.free_plan = Plan.objects.get(code="free")
        self.subscription = Subscription.objects.create(
            tenant=self.tenant,
            plan=self.free_plan,
            status=Subscription.Status.ACTIVE,
            starts_at=timezone.now(),
        )
        self.other_subscription = Subscription.objects.create(
            tenant=self.other_tenant,
            plan=Plan.objects.get(code="pro"),
            status=Subscription.Status.ACTIVE,
            starts_at=timezone.now(),
        )
        Project.objects.create(name="Existing project", owner=self.owner.pk)
        self.client = self.authenticated_client(self.owner)

    def authenticated_client(self, user):
        client = APIClient()
        token = str(RefreshToken.for_user(user).access_token)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        client.defaults["HTTP_HOST"] = "workspace.test"
        return client

    def test_authenticated_user_can_list_configured_plans(self):
        response = self.client.get("/api/plans/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {plan["code"] for plan in response.data["results"]},
            {"free", "pro", "business"},
        )
        self.assertEqual(response.data["results"][0]["currency"], "USD")

    def test_anonymous_user_cannot_list_plans(self):
        response = APIClient().get("/api/plans/")

        self.assertEqual(response.status_code, 401)

    def test_current_subscription_and_usage_are_for_selected_tenant_only(self):
        response = self.client.get("/api/subscription/")
        usage = self.client.get("/api/subscription/usage/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["organization"], "workspace")
        self.assertEqual(response.data["plan"]["code"], "free")
        self.assertEqual(usage.status_code, 200)
        self.assertEqual(usage.data["projects"]["used"], 1)
        self.assertEqual(usage.data["projects"]["limit"], 3)
        self.assertEqual(usage.data["users"]["used"], 1)

    def test_unlimited_usage_limits_report_null_remaining(self):
        self.free_plan.user_limit = None
        self.free_plan.usage_limits = {"projects": None}
        self.free_plan.save(update_fields=["user_limit", "usage_limits"])

        response = self.client.get("/api/subscription/usage/")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["users"]["limit"])
        self.assertIsNone(response.data["users"]["remaining"])
        self.assertIsNone(response.data["projects"]["limit"])
        self.assertIsNone(response.data["projects"]["remaining"])

    def test_member_cannot_read_another_organizations_subscription(self):
        client = self.authenticated_client(self.owner)
        response = client.get(
            "/api/subscription/",
            HTTP_X_ORGANIZATION=self.other_tenant.slug,
        )

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(self.other_subscription.plan.code, str(response.data))

    def test_subscription_endpoints_require_organization_context(self):
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.get("/api/subscription/", HTTP_HOST="testserver")

        self.assertEqual(response.status_code, 403)

    def test_project_creation_enforces_current_plan_limit(self):
        self.free_plan.usage_limits = {"projects": 1}
        self.free_plan.save(update_fields=["usage_limits"])
        response = self.client.post(
            "/api/projects/",
            {"name": "Over quota", "description": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Project.objects.filter(name="Over quota").exists())

    def test_zero_project_limit_blocks_project_creation(self):
        self.free_plan.usage_limits = {"projects": 0}
        self.free_plan.save(update_fields=["usage_limits"])

        response = self.client.post(
            "/api/projects/",
            {"name": "Zero quota", "description": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Project.objects.filter(name="Zero quota").exists())

    def test_future_subscription_start_blocks_usage_and_project_creation(self):
        self.subscription.starts_at = timezone.now() + timedelta(days=1)
        self.subscription.save(update_fields=["starts_at"])

        usage = self.client.get("/api/subscription/usage/")
        create = self.client.post(
            "/api/projects/",
            {"name": "Not started", "description": ""},
            format="json",
        )

        self.assertEqual(usage.status_code, 403)
        self.assertEqual(create.status_code, 403)

    def test_member_creation_enforces_current_plan_user_limit(self):
        self.free_plan.user_limit = 1
        self.free_plan.save(update_fields=["user_limit"])
        extra_user = get_user_model().objects.create_user(
            username="extra-subscription-user",
            password="test-password",
        )

        with self.assertRaisesMessage(
            ValidationError,
            "The organization's user limit has been reached.",
        ):
            Membership.objects.create(user=extra_user, tenant=self.tenant)

    def test_cancellation_requires_organization_admin(self):
        member = get_user_model().objects.create_user(
            username="workspace-member",
            password="test-password",
        )
        Membership.objects.create(user=member, tenant=self.tenant)

        response = self.authenticated_client(member).post(
            "/api/subscription/cancel/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.subscription.refresh_from_db()
        self.assertFalse(self.subscription.cancel_at_period_end)

    def test_organization_admin_can_cancel_past_due_subscription(self):
        self.subscription.status = Subscription.Status.PAST_DUE
        self.subscription.save(update_fields=["status"])

        response = self.client.post(
            "/api/subscription/cancel/",
            {"immediately": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], Subscription.Status.CANCELED)

    def test_past_due_subscription_does_not_allow_projects_or_usage(self):
        self.subscription.status = Subscription.Status.PAST_DUE
        self.subscription.save(update_fields=["status"])

        usage = self.client.get("/api/subscription/usage/")
        create = self.client.post(
            "/api/projects/",
            {"name": "Past due project", "description": ""},
            format="json",
        )

        self.assertEqual(usage.status_code, 403)
        self.assertEqual(create.status_code, 403)

    def test_subscription_data_changes_are_read_from_central_store(self):
        self.free_plan.price = "1.25"
        self.free_plan.billing_interval = Plan.BillingInterval.YEARLY
        self.free_plan.save(update_fields=["price", "billing_interval"])

        response = self.client.get("/api/plans/")
        free = next(plan for plan in response.data["results"] if plan["code"] == "free")

        self.assertEqual(free["price"], "1.25")
        self.assertEqual(free["billing_interval"], "yearly")


class SubscriptionLifecycleTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Lifecycle",
            slug="lifecycle",
            domain="lifecycle.test",
            database_alias="default",
        )
        self.subscription = Subscription.objects.create(
            tenant=self.tenant,
            plan=Plan.objects.get(code="pro"),
            status=Subscription.Status.ACTIVE,
            starts_at=timezone.now(),
            current_period_start=timezone.now() - timedelta(days=1),
            current_period_end=timezone.now() + timedelta(days=1),
        )

    def test_expiry_task_retries_database_failures_with_a_bound(self):
        task = expire_subscriptions._get_current_object()

        self.assertEqual(task.autoretry_for, (OperationalError,))
        self.assertTrue(task.retry_backoff)
        self.assertTrue(task.retry_jitter)
        self.assertEqual(task.retry_kwargs["max_retries"], 3)

    def test_scheduled_cancellation_becomes_canceled_at_period_end(self):
        scheduled = cancel_subscription(self.subscription)
        self.assertTrue(scheduled.cancel_at_period_end)
        self.assertEqual(scheduled.status, Subscription.Status.ACTIVE)

        count = expire_due_subscriptions(now=timezone.now() + timedelta(days=2))

        self.assertEqual(count, 1)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.CANCELED)
        self.assertIsNotNone(self.subscription.ended_at)

    def test_non_canceled_subscription_expires_at_period_end(self):
        count = expire_due_subscriptions(now=timezone.now() + timedelta(days=2))

        self.assertEqual(count, 1)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.EXPIRED)

    def test_expired_trial_is_expired_without_period_end(self):
        self.subscription.status = Subscription.Status.TRIALING
        self.subscription.trial_ends_at = timezone.now() - timedelta(seconds=1)
        self.subscription.current_period_end = None
        self.subscription.save(update_fields=["status", "trial_ends_at", "current_period_end"])

        count = expire_due_subscriptions()

        self.assertEqual(count, 1)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.EXPIRED)

    def test_immediate_cancellation_records_end_time(self):
        canceled = cancel_subscription(self.subscription, immediately=True)

        self.assertEqual(canceled.status, Subscription.Status.CANCELED)
        self.assertIsNotNone(canceled.canceled_at)
        self.assertIsNotNone(canceled.ended_at)

    def test_free_subscription_without_billing_period_cancels_immediately(self):
        free = Subscription.objects.create(
            tenant=Tenant.objects.create(
                name="Free cancellation",
                slug="free-cancellation",
                domain="free-cancellation.test",
                database_alias="default",
            ),
            plan=Plan.objects.get(code="free"),
            status=Subscription.Status.ACTIVE,
            starts_at=timezone.now(),
        )

        canceled = cancel_subscription(free)

        self.assertEqual(canceled.status, Subscription.Status.CANCELED)
        self.assertFalse(canceled.cancel_at_period_end)
        self.assertIsNotNone(canceled.ended_at)

    def test_provider_state_requires_verified_matching_event(self):
        self.subscription.provider_subscription_id = "provider-reference"
        self.subscription.save(update_fields=["provider_subscription_id"])
        with self.assertRaisesMessage(PermissionDenied, "verified"):
            apply_verified_provider_event(self.subscription, {"status": "active"})

        event = VerifiedSubscriptionEvent(
            event_id="evt-verified",
            subscription_reference="provider-reference",
            status=Subscription.Status.ACTIVE,
            period_start=timezone.now(),
            period_end=timezone.now() + timedelta(days=30),
        )
        updated = apply_verified_provider_event(self.subscription, event)

        self.assertEqual(updated.status, Subscription.Status.ACTIVE)
        self.assertGreater(updated.current_period_end, updated.current_period_start)

    def test_expiry_task_does_not_touch_free_subscription_without_period_end(self):
        free = Subscription.objects.create(
            tenant=Tenant.objects.create(
                name="Free lifecycle",
                slug="free-lifecycle",
                domain="free-lifecycle.test",
                database_alias="default",
            ),
            plan=Plan.objects.get(code="free"),
            status=Subscription.Status.ACTIVE,
            starts_at=timezone.now(),
        )
        count = expire_due_subscriptions(now=timezone.now() + timedelta(days=365))
        free.refresh_from_db()

        self.assertEqual(count, 1)
        self.assertEqual(free.status, Subscription.Status.ACTIVE)

    def test_expire_subscriptions_task_closes_connections(self):
        with patch("apps.subscriptions.tasks.close_old_connections") as mock_close:
            expire_subscriptions()
            self.assertEqual(mock_close.call_count, 2)
