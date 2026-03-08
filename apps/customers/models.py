"""
Public-schema models for multi-tenant School ERP.
School extends TenantMixin; each school gets its own PostgreSQL schema.
"""
from django.db import models
from django_tenants.models import TenantMixin, DomainMixin


class School(TenantMixin):
    """
    Tenant model: each school has its own schema (e.g. school_001).
    Lives in public schema; Domain model links domain names to schools.
    """
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True, help_text="Unique school code e.g. school_001")
    subscription_plan = models.ForeignKey(
        "core.Plan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schools",
    )
    created_on = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, db_index=True)
    address = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    # Custom branding (Pro Plan)
    logo = models.ImageField(upload_to="school_logos/", blank=True, null=True)
    theme_color = models.CharField(max_length=20, blank=True, default="#4F46E5")
    header_text = models.CharField(max_length=200, blank=True)
    custom_domain = models.CharField(max_length=255, blank=True)
    # Dedicated hosting
    is_single_tenant = models.BooleanField(default=False)

    auto_create_schema = True
    auto_drop_schema = False

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def has_plan_module(self, module: str) -> bool:
        if not self.subscription_plan:
            return True
        return self.subscription_plan.has_module(module)

    def is_pro_plan(self) -> bool:
        if not self.subscription_plan:
            return False
        return self.subscription_plan.plan_type in ("PRO", "ENTERPRISE")

    @property
    def is_pro_plan_property(self) -> bool:
        """Property for Django templates ({% if school.is_pro_plan_property %})."""
        return self.is_pro_plan()


class Domain(DomainMixin):
    """Links domain names to School tenants. Required by django-tenants."""
    pass


class PlatformSettings(models.Model):
    """Platform-wide settings stored in public schema."""
    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField(default=dict, blank=True)
    updated_on = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.key
