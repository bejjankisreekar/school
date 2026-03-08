from django.contrib import admin
from django_tenants.admin import TenantAdminMixin

from .models import School, Domain, PlatformSettings


@admin.register(School)
class SchoolAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "schema_name", "code", "subscription_plan", "created_on", "is_active")
    list_filter = ("is_active", "subscription_plan")
    search_fields = ("name", "code")


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "tenant", "is_primary")


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    list_display = ("key", "updated_on")
