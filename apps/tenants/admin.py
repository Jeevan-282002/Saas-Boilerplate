from django.contrib import admin

from apps.tenants.models import Membership, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "domain", "database_alias", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug", "domain"]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "tenant", "role", "created_at"]
    list_filter = ["role", "tenant"]
    search_fields = ["user__username", "user__email", "tenant__name"]
