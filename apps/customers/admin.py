from django.contrib import admin
from django_tenants.admin import TenantAdminMixin

from .models import (
    Coupon,
    Domain,
    Feature,
    Plan,
    PlatformBillingReceipt,
    PlatformInvoice,
    PlatformInvoicePayment,
    PlatformSettings,
    SaaSPlatformPayment,
    School,
    SchoolSubscription,
    SubscriptionPlan,
)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price_per_student", "billing_cycle", "is_active", "created_at")
    list_filter = ("billing_cycle", "is_active")
    filter_horizontal = ("features",)


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "description")


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price_per_student", "duration_days", "is_active")
    list_filter = ("is_active",)


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_type", "discount_value", "used_count", "max_usage", "valid_to", "is_active")
    list_filter = ("discount_type", "is_active")
    search_fields = ("code",)


@admin.register(PlatformInvoice)
class PlatformInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "school",
        "year",
        "month",
        "final_amount",
        "status",
        "due_date",
        "created_on",
    )
    list_filter = ("status", "year", "month")
    search_fields = ("invoice_number", "school__code", "school__name")
    raw_id_fields = ("school", "subscription")
    date_hierarchy = "due_date"


@admin.register(PlatformInvoicePayment)
class PlatformInvoicePaymentAdmin(admin.ModelAdmin):
    list_display = (
        "paid_on",
        "invoice",
        "school",
        "amount_paid",
        "payment_mode",
        "transaction_id",
        "recorded_by",
    )
    list_filter = ("payment_mode",)
    search_fields = ("transaction_id", "invoice__invoice_number", "school__code")
    raw_id_fields = ("school", "invoice", "recorded_by")


@admin.register(PlatformBillingReceipt)
class PlatformBillingReceiptAdmin(admin.ModelAdmin):
    list_display = ("receipt_number", "payment", "pdf_url", "generated_on")
    search_fields = ("receipt_number",)


@admin.register(SaaSPlatformPayment)
class SaaSPlatformPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "payment_date",
        "school",
        "amount",
        "payment_method",
        "reference",
        "internal_receipt_no",
        "school_generated_invoice",
        "recorded_by",
        "created_at",
    )
    list_filter = ("payment_method", "payment_date")
    search_fields = (
        "school__code",
        "school__name",
        "reference",
        "internal_receipt_no",
        "notes",
    )
    raw_id_fields = ("school", "subscription", "recorded_by", "school_generated_invoice")
    date_hierarchy = "payment_date"


@admin.register(SchoolSubscription)
class SchoolSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("school", "plan", "status", "start_date", "end_date", "students_count", "is_current", "coupon")
    list_filter = ("status", "is_current")
    raw_id_fields = ("school", "plan", "coupon")
    search_fields = ("school__code", "school__name")


@admin.register(School)
class SchoolAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "schema_name",
        "code",
        "school_status",
        "contact_person",
        "plan",
        "billing_plan",
        "trial_end_date",
        "created_on",
        "is_active",
    )
    list_filter = ("is_active", "school_status", "plan", "billing_plan")
    search_fields = ("name", "code", "contact_person")


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "tenant", "is_primary")


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    list_display = ("key", "updated_on")
