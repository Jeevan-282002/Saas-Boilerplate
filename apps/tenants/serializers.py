from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.tenants.models import AuditLog, Invitation, Membership


class OrganizationCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, trim_whitespace=True)

    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - {"name"}:
            raise serializers.ValidationError(
                {"non_field_errors": ["Only the organization name may be provided."]}
            )
        return super().to_internal_value(data)


class OrganizationMembershipSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source="tenant_id", read_only=True)
    name = serializers.CharField(source="tenant.name", read_only=True)
    slug = serializers.CharField(source="tenant.slug", read_only=True)
    domain = serializers.CharField(source="tenant.domain", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "name", "slug", "domain", "role"]


class MemberUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ["id", "username", "email", "first_name", "last_name"]


class MembershipSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "role", "created_at"]


class MemberRoleUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Membership.Role.choices)


class InvitationSerializer(serializers.ModelSerializer):
    invited_by_username = serializers.CharField(
        source="invited_by.username",
        read_only=True,
        default=None,
    )
    token = serializers.CharField(read_only=True, required=False)

    class Meta:
        model = Invitation
        fields = [
            "id",
            "email",
            "role",
            "status",
            "invited_by_username",
            "expires_at",
            "created_at",
            "accepted_at",
            "revoked_at",
            "token",
        ]


class InvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(
        choices=Membership.Role.choices,
        default=Membership.Role.MEMBER,
    )


class InvitationAcceptSerializer(serializers.Serializer):
    token = serializers.CharField()


class AuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(
        source="actor.username",
        read_only=True,
        default=None,
    )

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "action",
            "resource_type",
            "resource_id",
            "actor_username",
            "metadata",
            "ip_address",
            "created_at",
        ]
