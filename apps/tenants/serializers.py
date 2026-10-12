from rest_framework import serializers

from apps.tenants.models import Membership


class OrganizationMembershipSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source="tenant_id", read_only=True)
    name = serializers.CharField(source="tenant.name", read_only=True)
    slug = serializers.CharField(source="tenant.slug", read_only=True)
    domain = serializers.CharField(source="tenant.domain", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "name", "slug", "domain", "role"]
