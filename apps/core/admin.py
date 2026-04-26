from django.contrib import admin

from .models import (
    AnalyticsField,
    ContactEnquiry,
    Plan,
    SchoolEnrollmentRequest,
    SchoolSubscription,
    SubscriptionPlan,
)


@admin.register(AnalyticsField)
class AnalyticsFieldAdmin(admin.ModelAdmin):
    """Public-schema registry for report/analytics dimensions (global across all schools)."""

    list_display = ("field_key", "display_label", "category", "display_order", "is_active")
    list_filter = ("is_active", "category")
    search_fields = ("field_key", "display_label", "category")
    ordering = ("category", "field_key", "display_order", "display_label")


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "plan_type")
    list_filter = ("plan_type",)


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "billing_cycle", "is_active", "created_on")
    list_filter = ("billing_cycle", "is_active")
    search_fields = ("name",)


@admin.register(SchoolSubscription)
class SchoolSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("school", "plan", "start_date", "end_date", "is_trial", "is_active")
    list_filter = ("is_trial", "is_active")
    search_fields = ("school__name",)


@admin.register(ContactEnquiry)
class ContactEnquiryAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "school_name", "created_at", "is_read")
    list_filter = ("is_read",)


@admin.register(SchoolEnrollmentRequest)
class SchoolEnrollmentRequestAdmin(admin.ModelAdmin):
    list_display = ("institution_name", "email", "status", "created_at", "school")
    list_filter = ("status",)
    search_fields = ("institution_name", "email", "contact_name")
    readonly_fields = ("created_at", "reviewed_at", "reviewed_by", "school", "provisioned_schema_name")
