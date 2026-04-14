"""
Public-schema models for multi-tenant School ERP.
School extends TenantMixin; each school gets its own PostgreSQL schema.
"""
from django.conf import settings
from django.db import models
from django_tenants.models import TenantMixin, DomainMixin


class Feature(models.Model):
    """Module or capability that can be enabled per plan or per school."""
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True, help_text="Unique code, e.g. students, fees, payroll")
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


# Sellable SaaS tiers (superadmin pickers ignore legacy rows like Growth in DB).
SALE_SAAS_PLAN_NAMES = ("Starter", "Standard", "Enterprise")


class Plan(models.Model):
    """Product tier (logical `plans` table): Starter / Enterprise, per-student pricing and billing cycle."""

    class BillingCycle(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    name = models.CharField(max_length=100)
    price_per_student = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Price per student per month (INR); for yearly cycle this is still the monthly-equivalent display rate unless you adjust manually.",
    )
    billing_cycle = models.CharField(
        max_length=20,
        choices=BillingCycle.choices,
        default=BillingCycle.MONTHLY,
        db_index=True,
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Inactive plans are hidden from assignment pickers.",
    )
    features = models.ManyToManyField(
        Feature,
        related_name="plans",
        blank=True,
        help_text="Features included in this plan",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["price_per_student"]

    def __str__(self) -> str:
        return self.name

    def get_feature_codes(self):
        """Return set of feature codes for this plan."""
        return set(self.features.values_list("code", flat=True))

    @classmethod
    def sale_tiers(cls):
        """Starter and Enterprise only — use for dropdowns and plan changes."""
        return cls.objects.filter(
            name__in=SALE_SAAS_PLAN_NAMES,
            is_active=True,
        ).order_by("price_per_student")


class SubscriptionPlan(models.Model):
    """Internal billing state: trial period vs paid tier (maps to Starter/Enterprise). Not shown as a separate product."""
    PLAN_CHOICES = [
        ("trial", "Trial"),
        ("basic", "Basic"),
        ("pro", "Pro"),
    ]
    name = models.CharField(max_length=50, choices=PLAN_CHOICES, unique=True)
    price_per_student = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Price per student per month. Basic=39, Pro=59, Trial=0",
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
        return f"{self.name.title()} (Rs.{self.price_per_student}/student/month)"


# ORM feature codes used by @feature_required / request.school_features.
# Merged with plan features when enabled_features_override is not set, so new schools
# always get core modules even if saas_plan is missing. Override alone is authoritative.
DEFAULT_CORE_SCHOOL_FEATURES = frozenset(
    {
        "students",
        "teachers",
        "attendance",
        "exams",
        "timetable",
        "homework",
        "reports",
        "fees",
    }
)


class School(TenantMixin):
    """
    Tenant model: each school has its own schema (e.g. school_001).
    Lives in public schema; Domain model links domain names to schools.
    """

    class SchoolStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        TRIAL = "trial", "Trial"
        SUSPENDED = "suspended", "Suspended"

    class InstitutionType(models.TextChoices):
        SCHOOL = "SCHOOL", "School"
        INTERMEDIATE_COLLEGE = "INTERMEDIATE_COLLEGE", "Intermediate College"
        DEGREE_COLLEGE = "DEGREE_COLLEGE", "Degree College"
        PG_COLLEGE = "PG_COLLEGE", "PG College"
        UNIVERSITY = "UNIVERSITY", "University"

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True, help_text="Unique school code e.g. school_001")
    institution_type = models.CharField(
        max_length=30,
        choices=InstitutionType.choices,
        default=InstitutionType.SCHOOL,
        db_index=True,
        help_text="Institution category (used for future workflow/label variations).",
    )
    contact_person = models.CharField(
        max_length=200,
        blank=True,
        help_text="Primary billing or admin contact name",
    )
    school_status = models.CharField(
        max_length=20,
        choices=SchoolStatus.choices,
        default=SchoolStatus.ACTIVE,
        db_index=True,
        help_text="Lifecycle: Active, Inactive, Trial, or Suspended (syncs tenant is_active for access).",
    )
    saas_plan = models.ForeignKey(
        Plan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schools",
        help_text="Starter, Standard, or Enterprise — controls which modules are available",
    )
    enabled_features_override = models.JSONField(
        default=None,
        null=True,
        blank=True,
        help_text="Optional: list of feature codes enabled for this school. If set, overrides plan defaults.",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schools",
        help_text="Trial / paid billing record (trial, basic, pro); pairs with Starter or Enterprise",
    )
    subscription_plan = models.ForeignKey(
        "core.Plan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schools",
        help_text="Deprecated core.Plan link; use saas_plan (Starter/Enterprise)",
    )
    trial_end_date = models.DateField(null=True, blank=True)
    created_on = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, db_index=True)
    address = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    date_of_establishment = models.DateField(
        null=True,
        blank=True,
        help_text="Official date the institution was established or recognized.",
    )
    website = models.URLField(max_length=500, blank=True, help_text="Official website URL.")
    registration_number = models.CharField(
        max_length=120,
        blank=True,
        help_text="Government / board registration or UDISE / affiliation number.",
    )
    board_affiliation = models.CharField(
        max_length=120,
        blank=True,
        help_text="e.g. CBSE, ICSE, State Board, IB.",
    )
    # Custom branding (Pro Plan)
    logo = models.ImageField(upload_to="school_logos/", blank=True, null=True)
    theme_color = models.CharField(max_length=20, blank=True, default="#4F46E5")
    header_text = models.CharField(max_length=200, blank=True)

    class PayslipFormat(models.TextChoices):
        CORPORATE = "corporate", "Corporate — modern cards (recommended)"
        CLASSIC = "classic", "Classic — single-sheet tables"
        MINIMAL = "minimal", "Minimal — compact one-page"

    payslip_format = models.CharField(
        max_length=20,
        choices=PayslipFormat.choices,
        default=PayslipFormat.CORPORATE,
        help_text="Layout for employee payslips (on-screen view and PDF).",
    )

    # Timetable: one published schedule profile (tenant-scoped profile id).
    # Stored in public schema so teacher/student portals consistently pick one profile.
    timetable_current_profile_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="ScheduleProfile id to treat as the currently published timetable (optional).",
    )

    custom_domain = models.CharField(max_length=255, blank=True)
    # Dedicated hosting
    is_single_tenant = models.BooleanField(default=False)

    platform_control_meta = models.JSONField(
        default=dict,
        blank=True,
        help_text="Super Admin Control Center: limits, plan duration, disable_login, role_permissions JSON, etc.",
    )

    auto_create_schema = True
    auto_drop_schema = False

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def save(self, *args, **kwargs):
        if self.school_status in (self.SchoolStatus.INACTIVE, self.SchoolStatus.SUSPENDED):
            self.is_active = False
        elif self.school_status in (self.SchoolStatus.ACTIVE, self.SchoolStatus.TRIAL):
            self.is_active = True
        super().save(*args, **kwargs)

    def get_enabled_feature_codes(self) -> set:
        """
        Return set of feature codes enabled for this school.
        - If `enabled_features_override` is set, use it only (no plan merge).
          Legacy `staff` is treated as `teachers` for module gates.
        - Otherwise merge `saas_plan` features with DEFAULT_CORE_SCHOOL_FEATURES so new
          schools without a plan still get Teachers, Students, etc.
        """
        if self.enabled_features_override is not None:
            codes = set(self.enabled_features_override)
            if "staff" in codes and "teachers" not in codes:
                codes.add("teachers")
            return codes
        plan_codes = set(self.saas_plan.get_feature_codes()) if self.saas_plan else set()
        return plan_codes | set(DEFAULT_CORE_SCHOOL_FEATURES)

    def has_feature(self, feature: str) -> bool:
        """Check if school has access to feature via plan or override."""
        codes = self.get_enabled_feature_codes()
        return feature in codes

    def has_plan_module(self, module: str) -> bool:
        """Alias for has_feature (backward compat)."""
        return self.has_feature(module)

    def is_pro_plan(self) -> bool:
        """True if school is on Enterprise tier (or legacy Advance / Pro)."""
        if self.saas_plan:
            t = (self.saas_plan.name or "").strip().lower()
            return t in ("enterprise", "standard", "advance")
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


class Coupon(models.Model):
    """Discount codes (fixed INR or percentage) for subscription assignment."""

    class DiscountType(models.TextChoices):
        FIXED = "fixed", "Fixed amount (₹)"
        PERCENTAGE = "percentage", "Percentage"

    code = models.CharField(max_length=40, unique=True, db_index=True)
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices)
    discount_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Fixed: rupees off per bill line; Percentage: 0–100.",
    )
    max_usage = models.PositiveIntegerField(
        default=0,
        help_text="0 = unlimited redemptions.",
    )
    used_count = models.PositiveIntegerField(default=0)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.code} ({self.get_discount_type_display()})"


class SchoolSubscription(models.Model):
    """Maps a school to a product plan for a period; optional coupon and free months (audit trail)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        TRIAL = "trial", "Trial"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="subscription_records",
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="school_subscriptions",
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    students_count = models.PositiveIntegerField(
        default=0,
        help_text="Billable student headcount snapshot when assigned.",
    )
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscription_uses",
    )
    free_months_applied = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    is_current = models.BooleanField(
        default=False,
        db_index=True,
        help_text="At most one current row per school.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["school"],
                condition=models.Q(is_current=True),
                name="customers_schoolsub_unique_current_school",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.school.code} → {self.plan.name} ({self.status})"


class SaaSPlatformPayment(models.Model):
    """
    Money received by the platform operator from a school (subscription / license).
    Distinct from fee payments inside a tenant schema.
    """

    class PaymentMethod(models.TextChoices):
        UPI = "upi", "UPI"
        BANK_TRANSFER = "bank_transfer", "Bank transfer (NEFT / RTGS / IMPS)"
        CASH = "cash", "Cash"
        CARD = "card", "Card / payment gateway"
        CHEQUE = "cheque", "Cheque"
        OTHER = "other", "Other"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="saas_platform_payments",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField(db_index=True)
    payment_method = models.CharField(
        max_length=30,
        choices=PaymentMethod.choices,
        default=PaymentMethod.UPI,
    )
    reference = models.CharField(
        max_length=200,
        blank=True,
        help_text="UTR, transaction id, cheque no., or receipt reference",
    )
    notes = models.TextField(blank=True)
    internal_receipt_no = models.CharField(
        max_length=64,
        blank=True,
        help_text="Your internal voucher or receipt book number (for audits).",
    )
    service_period_start = models.DateField(
        null=True,
        blank=True,
        help_text="Optional: subscription or service period this payment covers (start).",
    )
    service_period_end = models.DateField(
        null=True,
        blank=True,
        help_text="Optional: subscription or service period this payment covers (end).",
    )
    subscription = models.ForeignKey(
        SchoolSubscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_payments",
        help_text="Optional link to the subscription period this payment covers.",
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_saas_platform_payments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        verbose_name = "platform subscription payment"
        verbose_name_plural = "platform subscription payments"

    def __str__(self) -> str:
        return f"{self.school.code} ₹{self.amount} on {self.payment_date}"


class PlatformInvoice(models.Model):
    """
    SaaS billing invoice per school per calendar month (platform operator).
    Table: saas_invoices
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PARTIAL = "partial", "Partial"
        PAID = "paid", "Paid"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="platform_invoices",
    )
    subscription = models.ForeignKey(
        "SchoolSubscription",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_invoices",
        help_text="Subscription row this invoice was generated from.",
    )
    month = models.PositiveSmallIntegerField(help_text="1–12")
    year = models.PositiveSmallIntegerField()
    students_count = models.PositiveIntegerField(default=0)
    price_per_student = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    final_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    due_date = models.DateField(db_index=True)
    invoice_number = models.CharField(max_length=40, unique=True, db_index=True)
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "saas_invoices"
        ordering = ["-year", "-month", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "year", "month"],
                name="uniq_saas_invoice_school_period",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.invoice_number} — {self.school.code}"


class PlatformInvoicePayment(models.Model):
    """
    Payment applied against a platform SaaS invoice.
    Table: saas_invoice_payments
    """

    class PaymentMode(models.TextChoices):
        UPI = "upi", "UPI"
        CASH = "cash", "Cash"
        BANK = "bank", "Bank"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="platform_invoice_payments",
    )
    invoice = models.ForeignKey(
        PlatformInvoice,
        on_delete=models.CASCADE,
        related_name="invoice_payments",
    )
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices)
    transaction_id = models.CharField(max_length=200, blank=True)
    paid_on = models.DateTimeField(db_index=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_platform_invoice_payments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "saas_invoice_payments"
        ordering = ["-paid_on", "-id"]

    def __str__(self) -> str:
        return f"₹{self.amount_paid} → {self.invoice.invoice_number}"


class PlatformBillingReceipt(models.Model):
    """
    Receipt PDF metadata for a platform invoice payment.
    Table: saas_billing_receipts
    """

    payment = models.OneToOneField(
        PlatformInvoicePayment,
        on_delete=models.CASCADE,
        related_name="billing_receipt",
    )
    receipt_number = models.CharField(max_length=40, unique=True, db_index=True)
    pdf_url = models.CharField(
        max_length=500,
        blank=True,
        help_text="Path under MEDIA_ROOT or storage-relative URL key.",
    )
    generated_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "saas_billing_receipts"
        ordering = ["-generated_on"]

    def __str__(self) -> str:
        return self.receipt_number


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
