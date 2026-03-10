"""
Public-schema models for multi-tenant School ERP.
School extends TenantMixin; each school gets its own PostgreSQL schema.
"""
from django.db import models
from django_tenants.models import TenantMixin, DomainMixin


class SubscriptionPlan(models.Model):
    """Trial, Basic, Pro - per-student pricing, no fixed monthly fees."""
    PLAN_CHOICES = [
        ("trial", "Trial"),
        ("basic", "Basic"),
        ("pro", "Pro"),
    ]
    name = models.CharField(max_length=50, choices=PLAN_CHOICES, unique=True)
    price_per_student = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Price per student per year. Basic=39, Pro=59, Trial=0",
    )
    duration_days = models.IntegerField(
        default=365,
        help_text="Trial: 14, Basic/Pro: 365",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["price_per_student"]
        verbose_name = "Subscription Plan"
        verbose_name_plural = "Subscription Plans"

    def __str__(self) -> str:
        if self.name == "trial":
            return f"Trial ({self.duration_days} days)"
        return f"{self.name.title()} (Rs.{self.price_per_student}/student/year)"


class School(TenantMixin):
    """
    Tenant model: each school has its own schema (e.g. school_001).
    Lives in public schema; Domain model links domain names to schools.
    """
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True, help_text="Unique school code e.g. school_001")
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schools",
        help_text="Trial, Basic, or Pro",
    )
    subscription_plan = models.ForeignKey(
        "core.Plan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schools",
        help_text="Legacy - use plan instead",
    )
    trial_end_date = models.DateField(null=True, blank=True)
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

    def has_feature(self, feature: str) -> bool:
        """Check if plan includes feature. Uses apps.customers.subscription.has_feature."""
        from .subscription import has_feature as _has_feature
        return _has_feature(self, feature)

    def has_plan_module(self, module: str) -> bool:
        """Alias for has_feature (backward compat)."""
        return self.has_feature(module)

    def is_pro_plan(self) -> bool:
        if self.plan:
            return (self.plan.name or "").lower() == "pro"
        if self.subscription_plan:
            return self.subscription_plan.plan_type in ("PRO", "ENTERPRISE")
        return False

    @property
    def is_pro_plan_property(self) -> bool:
        return self.is_pro_plan()

    def is_trial_expired(self) -> bool:
        from .subscription import is_trial_expired
        return is_trial_expired(self)


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
