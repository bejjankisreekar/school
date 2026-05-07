from __future__ import annotations

from django.db import models


class ControlCenterSettings(models.Model):
    """
    Singleton (pk=1) configuration for the Super Admin Control Center.
    Created on first access via get_solo().
    """

    class BillingCycleDefault(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    class ThemeDefault(models.TextChoices):
        LIGHT = "light", "Light"
        DARK = "dark", "Dark"

    updated_at = models.DateTimeField(auto_now=True)

    platform_name = models.CharField(max_length=120, default="Campus ERP")
    logo = models.ImageField(upload_to="control_center/", blank=True, null=True)
    default_language = models.CharField(max_length=10, default="en")
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    default_theme = models.CharField(
        max_length=16,
        choices=ThemeDefault.choices,
        default=ThemeDefault.LIGHT,
        help_text="Default Control Center appearance for new sessions (browser still wins if user toggled).",
    )

    default_billing_cycle = models.CharField(
        max_length=16,
        choices=BillingCycleDefault.choices,
        default=BillingCycleDefault.MONTHLY,
    )
    grace_period_days = models.PositiveSmallIntegerField(
        default=14,
        help_text="Days after due before treating as overdue in UI copy (informational).",
    )
    gst_enabled = models.BooleanField(default=True)
    gst_percent = models.DecimalField(max_digits=5, decimal_places=2, default=18)
    currency_code = models.CharField(max_length=8, default="INR")
    extra_charges_enabled = models.BooleanField(default=True)
    concession_enabled = models.BooleanField(default=True)

    email_notifications = models.BooleanField(default=True)
    sms_notifications = models.BooleanField(default=False)
    template_payment_reminder = models.TextField(blank=True, default="")
    template_invoice = models.TextField(blank=True, default="")
    template_welcome = models.TextField(blank=True, default="")

    admin_session_timeout_minutes = models.PositiveSmallIntegerField(default=60)
    password_min_length = models.PositiveSmallIntegerField(default=8)
    password_require_special = models.BooleanField(default=False)
    enable_two_factor = models.BooleanField(default=False)

    auto_invoice_generation = models.BooleanField(default=False)
    invoice_generation_day = models.PositiveSmallIntegerField(default=1)
    auto_mark_overdue_days = models.PositiveSmallIntegerField(
        default=14,
        help_text="Issued invoices past due by this many days are treated as overdue in billing UIs.",
    )

    razorpay_key_id = models.CharField(max_length=120, blank=True, default="")
    razorpay_key_secret = models.CharField(max_length=200, blank=True, default="")
    twilio_account_sid = models.CharField(max_length=80, blank=True, default="")
    twilio_auth_token = models.CharField(max_length=120, blank=True, default="")

    maintenance_mode = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Control Center settings"
        verbose_name_plural = "Control Center settings"

    def __str__(self) -> str:
        return "Control Center settings"

    @classmethod
    def get_solo(cls) -> ControlCenterSettings:
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={},
        )
        return obj


class FeatureCategory(models.TextChoices):
    ACADEMIC = "academic", "Academic"
    OPERATIONS = "operations", "Operations"
    EXAMS = "exams", "Exams"
    COMMUNICATION = "communication", "Communication"
    FINANCE = "finance", "Finance"


class Feature(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=100, unique=True, db_index=True)
    category = models.CharField(
        max_length=32,
        choices=FeatureCategory.choices,
        default=FeatureCategory.ACADEMIC,
        db_index=True,
    )

    class Meta:
        ordering = ["category", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class PlanName(models.TextChoices):
    BASIC = "basic", "Basic"
    PRO = "pro", "Pro"
    PREMIUM = "premium", "Premium"


class Plan(models.Model):
    name = models.CharField(max_length=20, choices=PlanName.choices, unique=True, db_index=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    list_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    features = models.ManyToManyField(Feature, blank=True, related_name="plans")
    is_active = models.BooleanField(default=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.get_name_display()

