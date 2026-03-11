from django.contrib import admin
from django_tenants.admin import TenantAdminMixin

from .models import School, Domain, PlatformSettings, SubscriptionPlan, Plan, Feature


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price_per_student", "created_at")
    filter_horizontal = ("features",)


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "description")


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price_per_student", "duration_days", "is_active")
    list_filter = ("is_active",)


@admin.register(School)
class SchoolAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ("name", "schema_name", "code", "saas_plan", "plan", "trial_end_date", "subscription_plan", "created_on", "is_active")
    list_filter = ("is_active", "plan", "subscription_plan")
    search_fields = ("name", "code")


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "tenant", "is_primary")


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    list_display = ("key", "updated_on")
