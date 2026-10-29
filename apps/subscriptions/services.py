from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.subscriptions.models import Subscription
from apps.tenants.models import Membership

ENTITLED_STATUSES = {Subscription.Status.ACTIVE, Subscription.Status.TRIALING}


def get_subscription_for_tenant(tenant, *, for_update=False):
    try:
        if for_update:
            subscriptions = Subscription.objects.using("default").select_for_update()
        else:
            subscriptions = Subscription.objects.using("default").select_related("plan")
        return subscriptions.get(tenant_id=tenant.pk)
    except Subscription.DoesNotExist:
        raise PermissionDenied("This organization does not have a subscription.") from None


def get_current_subscription(tenant, *, for_update=False):
    subscription = get_subscription_for_tenant(tenant, for_update=for_update)
    now = timezone.now()
    if subscription.starts_at > now:
        raise PermissionDenied("The organization's subscription has not started.")
    if subscription.status == Subscription.Status.TRIALING:
        if subscription.trial_ends_at is None or subscription.trial_ends_at <= now:
            raise PermissionDenied("The organization's trial has expired.")
    elif subscription.status not in ENTITLED_STATUSES:
        raise PermissionDenied("The organization's subscription is not active.")
    if subscription.current_period_end and subscription.current_period_end <= now:
        raise PermissionDenied("The organization's subscription period has expired.")
    if subscription.current_period_start and subscription.current_period_start > now:
        raise PermissionDenied("The organization's subscription period has not started.")
    return subscription


def get_usage(subscription, tenant_database):
    from apps.projects.models import Project

    users = Membership.objects.using("default").filter(tenant_id=subscription.tenant_id).count()
    projects = Project.objects.using(tenant_database).count()
    return {
        "users": {
            "used": users,
            "limit": subscription.plan.user_limit,
            "remaining": (
                None
                if subscription.plan.user_limit is None
                else max(subscription.plan.user_limit - users, 0)
            ),
        },
        "projects": {
            "used": projects,
            "limit": subscription.plan.usage_limits.get("projects"),
            "remaining": (
                None
                if subscription.plan.usage_limits.get("projects") is None
                else max(subscription.plan.usage_limits["projects"] - projects, 0)
            ),
        },
    }


def enforce_project_limit(subscription, tenant_database):
    from apps.projects.models import Project

    limit = subscription.plan.usage_limits.get("projects")
    if limit is not None and Project.objects.using(tenant_database).count() >= limit:
        raise PermissionDenied("The organization's project limit has been reached.")


def enforce_user_limit(subscription):
    users = Membership.objects.using("default").filter(tenant_id=subscription.tenant_id).count()
    if subscription.plan.user_limit is not None and users >= subscription.plan.user_limit:
        raise PermissionDenied("The organization's user limit has been reached.")


def cancel_subscription(subscription, *, immediately=False):
    now = timezone.now()
    with transaction.atomic(using="default"):
        subscription = (
            Subscription.objects.using("default").select_for_update().get(pk=subscription.pk)
        )
        if subscription.status in {
            Subscription.Status.CANCELED,
            Subscription.Status.EXPIRED,
        }:
            return subscription
        if subscription.provider_subscription_id:
            raise PermissionDenied(
                "This provider-managed subscription requires a configured payment provider."
            )
        if immediately or subscription.current_period_end is None:
            subscription.status = Subscription.Status.CANCELED
            subscription.cancel_at_period_end = False
            subscription.canceled_at = now
            subscription.ended_at = now
        else:
            subscription.cancel_at_period_end = True
        subscription.save(
            using="default",
            update_fields=[
                "status",
                "cancel_at_period_end",
                "canceled_at",
                "ended_at",
                "updated_at",
            ],
        )
    return subscription


def expire_due_subscriptions(*, now=None):
    now = now or timezone.now()
    expired_count = 0
    with transaction.atomic(using="default"):
        due = (
            Subscription.objects.using("default")
            .select_for_update()
            .filter(
                Q(current_period_end__lte=now)
                | Q(
                    status=Subscription.Status.TRIALING,
                    trial_ends_at__lte=now,
                )
            )
            .exclude(
                status__in=[
                    Subscription.Status.CANCELED,
                    Subscription.Status.EXPIRED,
                ]
            )
        )
        for subscription in due:
            subscription.status = (
                Subscription.Status.CANCELED
                if subscription.cancel_at_period_end
                else Subscription.Status.EXPIRED
            )
            subscription.ended_at = now
            if subscription.status == Subscription.Status.CANCELED:
                subscription.canceled_at = now
            subscription.save(
                using="default",
                update_fields=["status", "canceled_at", "ended_at", "updated_at"],
            )
            expired_count += 1
    return expired_count


def apply_verified_provider_event(subscription, event):
    from apps.subscriptions.providers import VerifiedSubscriptionEvent

    if not isinstance(event, VerifiedSubscriptionEvent):
        raise PermissionDenied("A verified payment-provider event is required.")
    if event.subscription_reference != subscription.provider_subscription_id:
        raise PermissionDenied("The provider event does not match this subscription.")
    if event.status not in Subscription.Status.values:
        raise PermissionDenied("The provider event contains an unsupported status.")

    with transaction.atomic(using="default"):
        subscription = (
            Subscription.objects.using("default").select_for_update().get(pk=subscription.pk)
        )
        subscription.status = event.status
        subscription.current_period_start = event.period_start
        subscription.current_period_end = event.period_end
        subscription.save(
            using="default",
            update_fields=[
                "status",
                "current_period_start",
                "current_period_end",
                "updated_at",
            ],
        )
    return subscription
