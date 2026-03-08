from django.contrib import admin
from .models import Plan, SubscriptionPlan, SchoolSubscription


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
