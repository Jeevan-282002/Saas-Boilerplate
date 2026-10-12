from django.conf import settings
from django.db import models


class Tenant(models.Model):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=63, unique=True)
    domain = models.CharField(max_length=253, unique=True)
    database_alias = models.CharField(max_length=63)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

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

    def __str__(self) -> str:
        return f"{self.user} — {self.tenant}"
