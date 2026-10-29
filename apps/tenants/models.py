from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.utils import timezone


class Tenant(models.Model):
    class ProvisioningStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=63, unique=True)
    domain = models.CharField(max_length=253, unique=True)
    database_alias = models.CharField(max_length=63)
    is_active = models.BooleanField(default=True)
    provisioning_status = models.CharField(
        max_length=16,
        choices=ProvisioningStatus.choices,
        default=ProvisioningStatus.READY,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_memberships",
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["tenant__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "tenant"],
                name="unique_user_organization_membership",
            ),
        ]

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        is_new = self._state.adding
        if not is_new:
            is_new = not type(self).objects.using(using).filter(pk=self.pk).exists()

        if is_new:
            from apps.subscriptions.models import Subscription

            with transaction.atomic(using=using):
                Tenant.objects.using(using).select_for_update().get(pk=self.tenant_id)
                subscription = (
                    Subscription.objects.using("default")
                    .select_related("plan")
                    .filter(tenant_id=self.tenant_id)
                    .first()
                )
                if subscription:
                    now = timezone.now()
                    if (
                        subscription.status
                        not in {Subscription.Status.ACTIVE, Subscription.Status.TRIALING}
                        or (
                            subscription.current_period_end
                            and subscription.current_period_end <= now
                        )
                        or (
                            subscription.status == Subscription.Status.TRIALING
                            and (
                                not subscription.trial_ends_at or subscription.trial_ends_at <= now
                            )
                        )
                    ):
                        raise ValidationError("The organization's subscription is not active.")
                    if subscription.plan.user_limit is not None:
                        current_count = (
                            type(self).objects.using(using).filter(tenant_id=self.tenant_id).count()
                        )
                        if current_count >= subscription.plan.user_limit:
                            raise ValidationError("The organization's user limit has been reached.")
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} — {self.tenant} ({self.role})"


class Invitation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    email = models.EmailField(max_length=254)
    role = models.CharField(
        max_length=16,
        choices=Membership.Role.choices,
        default=Membership.Role.MEMBER,
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_invitations",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "email"],
                condition=models.Q(status="pending"),
                name="unique_pending_invitation_per_org_email",
            ),
        ]

    def is_valid(self) -> bool:
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()

    def __str__(self) -> str:
        return f"Invite for {self.email} to {self.tenant.name} ({self.status})"


class AuditLog(models.Model):
    class Action(models.TextChoices):
        INVITATION_CREATED = "invitation.created", "Invitation Created"
        INVITATION_ACCEPTED = "invitation.accepted", "Invitation Accepted"
        INVITATION_REVOKED = "invitation.revoked", "Invitation Revoked"
        MEMBER_ADDED = "member.added", "Member Added"
        MEMBER_ROLE_CHANGED = "member.role_changed", "Member Role Changed"
        MEMBER_REMOVED = "member.removed", "Member Removed"
        PROJECT_CREATED = "project.created", "Project Created"
        PROJECT_UPDATED = "project.updated", "Project Updated"
        PROJECT_DELETED = "project.deleted", "Project Deleted"
        ORGANIZATION_CREATED = "organization.created", "Organization Created"
        ORGANIZATION_UPDATED = "organization.updated", "Organization Updated"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_actions",
    )
    action = models.CharField(max_length=64, db_index=True)
    resource_type = models.CharField(max_length=64, db_index=True)
    resource_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["tenant", "action"]),
        ]

    def __str__(self) -> str:
        return f"[{self.created_at}] {self.tenant.slug}: {self.action} by {self.actor}"
