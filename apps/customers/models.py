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

    class SaaSBillingCycle(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

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
    # SaaS plan (v2) — controlled by apps.super_admin.Plan (new Control Center).
    plan = models.ForeignKey(
        "super_admin.Plan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=False,
        related_name="schools",
        help_text="Basic / Pro / Premium — controls which modules are available",
    )
    billing_plan = models.ForeignKey(
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
    is_archived = models.BooleanField(
        default=False,
        db_index=True,
        help_text="When True, the school is hidden from normal operations and tenant login is blocked; data is kept.",
    )
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

    # SaaS platform billing (per student) — Control Center Billing tab + API.
    billing_extra_per_student_month = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Extra platform charge per student per month (added to plan list price).",
    )
    billing_concession_per_student_month = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Concession/discount per student per month on the platform bill.",
    )
    saas_billing_cycle = models.CharField(
        max_length=16,
        choices=SaaSBillingCycle.choices,
        default=SaaSBillingCycle.MONTHLY,
        db_index=True,
        help_text="Whether the school is invoiced on a monthly or yearly SaaS cycle.",
    )
    billing_student_count_override = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="If set, Control Center billing uses this headcount instead of live tenant count.",
    )
    saas_billing_auto_renew = models.BooleanField(
        default=True,
        db_index=True,
        help_text="When enabled, the school is treated as opting into automatic renewal for SaaS billing workflows.",
    )
    saas_billing_complimentary_until = models.DateField(
        null=True,
        blank=True,
        help_text="Legacy: through this date (inclusive), invoices may be ₹0. Prefer saas_free_until_date.",
    )
    saas_service_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="Optional contract anchor: billing periods before this month are not issued unless overridden.",
    )
    saas_free_until_date = models.DateField(
        null=True,
        blank=True,
        help_text="Inclusive last day of free service; invoices for calendar periods ending on/before this date are blocked unless overridden.",
    )
    registration_date = models.DateField(
        null=True,
        blank=True,
        help_text="School / contract registration anchor for billing configuration.",
    )
    billing_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="First calendar day billable SaaS charges apply (defaults to day after free-until when set).",
    )

    auto_create_schema = True
    auto_drop_schema = False

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def create_schema(self, check_if_exists=False, sync_schema=True, verbosity=1):
        """
        Create tenant schema and apply TENANT_APPS migrations.

        After django-tenants ``create_schema`` (create + optional migrate), we always run
        ``migrate_schemas`` once more for this tenant so empty/partial schemas and
        skipped first passes still get ``school_data_*`` and other tenant tables.
        """
        from django_tenants.utils import get_public_schema_name

        if self.schema_name == get_public_schema_name():
            return super().create_schema(
                check_if_exists=check_if_exists,
                sync_schema=sync_schema,
                verbosity=verbosity,
            )

        super().create_schema(
            check_if_exists=check_if_exists,
            sync_schema=sync_schema,
            verbosity=verbosity,
        )
        # Always run tenant migrations after the mixin step: django-tenants skips migrate when
        # the schema already exists; super() can also leave the recorder/connection in a state
        # where the first migrate pass does not fully apply. A second migrate_schemas is cheap.
        if sync_schema:
            from apps.core.tenant_schema_repair import apply_tenant_migrations_for_school

            apply_tenant_migrations_for_school(self, verbosity=verbosity)

    def save(self, *args, **kwargs):
        if getattr(self, "is_archived", False):
            self.is_active = False
        elif self.school_status in (self.SchoolStatus.INACTIVE, self.SchoolStatus.SUSPENDED):
            self.is_active = False
        # Do not force is_active=True for ACTIVE/TRIAL: superadmin uses is_active=False as a soft
        # "inactivate" while keeping trial/active status (login still allowed; see allows_tenant_user_login).
        # Partial saves with update_fields=["school_status"] must still persist is_active;
        # otherwise the row keeps is_active=True and tenant users can still authenticate.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            uf = list(dict.fromkeys(list(update_fields)))
            if "school_status" in uf and "is_active" not in uf:
                uf.append("is_active")
            if "is_archived" in uf and "is_active" not in uf and getattr(self, "is_archived", False):
                uf.append("is_active")
            kwargs["update_fields"] = uf
        super().save(*args, **kwargs)

    def allows_tenant_user_login(self) -> bool:
        """Lifecycle gate for school staff and portals (not used for superadmin)."""
        if getattr(self, "is_archived", False):
            return False
        if self.school_status == self.SchoolStatus.SUSPENDED:
            return False
        if self.school_status == self.SchoolStatus.INACTIVE:
            return False
        # Soft inactivate: is_active False with trial/active — still allow sign-in (banner in middleware).
        return True

    @classmethod
    def allows_login_for_school_code(cls, code: str | None) -> bool:
        """Resolve School in public schema (safe when DB connection is on a tenant)."""
        return cls.school_login_block_reason_for_code(code) is None

    @classmethod
    def school_login_block_reason_for_code(cls, code: str | None) -> str | None:
        """
        If tenant login should be blocked for this school code, return a stable reason key
        for redirects and audit logs; otherwise None.
        """
        if not code:
            return None
        from django_tenants.utils import get_public_schema_name, schema_context

        with schema_context(get_public_schema_name()):
            sch = (
                cls.objects.filter(code=code)
                .only("school_status", "is_active", "is_archived")
                .first()
            )
        if sch is None:
            return None
        if getattr(sch, "is_archived", False):
            return "school_archived"
        if sch.school_status == cls.SchoolStatus.SUSPENDED:
            return "school_suspended"
        if sch.school_status == cls.SchoolStatus.INACTIVE:
            return "school_inactive_status"
        return None

    def _feature_codes_from_customer_subscription(self) -> set | None:
        """
        If the school has a current ``SchoolSubscription`` (customers.Plan) in effect,
        return that plan's feature codes from the database. Otherwise return None so callers
        fall back to ``super_admin.Plan`` / defaults.
        """
        from django.utils import timezone

        today = timezone.localdate()
        try:
            sub = (
                SchoolSubscription.objects.filter(school_id=self.pk, is_current=True)
                .select_related("plan")
                .first()
            )
        except Exception:
            return None
        if not sub:
            return None
        if not getattr(sub, "is_active", True):
            return None
        if sub.status not in (SchoolSubscription.Status.ACTIVE, SchoolSubscription.Status.TRIAL):
            return None
        if sub.start_date > today:
            return None
        if sub.end_date and sub.end_date < today:
            return None
        return set(sub.plan.features.values_list("code", flat=True))

    def get_base_plan_feature_codes(self) -> set:
        """
        Feature codes from the school's **base** entitlement only (no add-ons).

        Same resolution order as ``get_enabled_feature_codes`` but without ``SchoolFeatureAddon``.
        Used by Control Center add-on UI so "Included in plan" matches enforcement.
        """
        from apps.core.plan_features import resolve_base_canonical_codes

        return set(resolve_base_canonical_codes(self))

    def get_enabled_feature_codes(self) -> set:
        """
        Return materialized feature codes for gates, middleware, and sidebars.

        Combines subscription / super_admin plan (see ``apps.core.plan_features``) with
        enabled ``SchoolFeatureAddon`` rows, then expands legacy route keys.
        """
        from apps.core.plan_features import build_enabled_materialized

        return set(build_enabled_materialized(self))

    def has_feature(self, feature: str) -> bool:
        """Check if school has access to feature via plan or paid add-on."""
        from apps.core.plan_features import has_feature_for_school

        return has_feature_for_school(self, feature)

    def has_plan_module(self, module: str) -> bool:
        """Alias for has_feature (backward compat)."""
        return self.has_feature(module)

    def is_pro_plan(self) -> bool:
        """True if school is on Enterprise tier (or legacy Advance / Pro)."""
        if self.billing_plan:
            return (self.billing_plan.name or "").lower() == "pro"
        if self.subscription_plan:
            return self.subscription_plan.plan_type in ("PRO", "ENTERPRISE")
        return False

    @property
    def is_pro_plan_property(self) -> bool:
        return self.is_pro_plan()

    def is_trial_expired(self) -> bool:
        from .subscription import is_trial_expired
        return is_trial_expired(self)

    def _billing_start_auto_from_free_service(self):
        """First billable day implied by free-until (inclusive last free day + 1)."""
        from datetime import timedelta

        fu = self.saas_free_until_date or self.saas_billing_complimentary_until
        if not fu:
            return None
        return fu + timedelta(days=1)

    @property
    def billing_start_is_manual_override(self) -> bool:
        """True when stored billing_start_date differs from the day-after-free default."""
        auto = self._billing_start_auto_from_free_service()
        if self.billing_start_date is None:
            return False
        if auto is not None:
            return self.billing_start_date != auto
        return True

    def saas_billing_monthly_breakdown(self, tenant_student_count: int) -> dict:
        """
        Monthly SaaS bill components.

        Uses ``billing_student_count_override`` when set; otherwise ``tenant_student_count``.

        Final (monthly) =
            (plan_price × n) + (extra_per_student × n) − (concession_per_student × n)
        """
        from decimal import Decimal, ROUND_HALF_UP

        tenant_n = max(0, int(tenant_student_count))
        if self.billing_student_count_override is not None:
            n = max(0, int(self.billing_student_count_override))
        else:
            n = tenant_n
        q2 = Decimal("0.01")

        def q(d: Decimal) -> str:
            return format(d.quantize(q2, rounding=ROUND_HALF_UP), "f")

        plan_price = Decimal(self.plan.price) if self.plan_id else Decimal("0")
        extra_ps = Decimal(self.billing_extra_per_student_month or 0)
        conc_ps = Decimal(self.billing_concession_per_student_month or 0)
        max_conc_ps = (plan_price + extra_ps).quantize(q2, rounding=ROUND_HALF_UP)
        if conc_ps > max_conc_ps:
            conc_ps = max_conc_ps
        if conc_ps < 0:
            conc_ps = Decimal("0")

        base_cost = (plan_price * n).quantize(q2, rounding=ROUND_HALF_UP)
        extra_cost = (extra_ps * n).quantize(q2, rounding=ROUND_HALF_UP)
        concession_cost = (conc_ps * n).quantize(q2, rounding=ROUND_HALF_UP)
        final_monthly = (base_cost + extra_cost - concession_cost).quantize(
            q2, rounding=ROUND_HALF_UP
        )
        if final_monthly < 0:
            final_monthly = Decimal("0").quantize(q2, rounding=ROUND_HALF_UP)

        is_yearly = self.saas_billing_cycle == self.SaaSBillingCycle.YEARLY
        final_period = (final_monthly * 12).quantize(q2, rounding=ROUND_HALF_UP) if is_yearly else final_monthly
        if final_period < 0:
            final_period = Decimal("0").quantize(q2, rounding=ROUND_HALF_UP)

        return {
            "student_count": n,
            "tenant_student_count": tenant_n,
            "uses_student_override": self.billing_student_count_override is not None,
            "plan_price_per_student": q(plan_price),
            "billing_extra_per_student_month": q(extra_ps),
            "billing_concession_per_student_month": q(conc_ps),
            "base_cost": q(base_cost),
            "extra_cost": q(extra_cost),
            "concession_cost": q(concession_cost),
            "final_monthly": q(final_monthly),
            "final_period": q(final_period),
            "is_yearly": is_yearly,
        }


class SchoolBillingAuditLog(models.Model):
    """Immutable audit trail for Control Center billing and related actions."""

    class Kind(models.TextChoices):
        BILLING_TERMS = "billing_terms", "Billing terms"
        STUDENT_OVERRIDE = "student_override", "Student count override"
        PLAN_CHANGE = "plan_change", "Plan change"
        STATUS = "status", "Account status"
        INVOICE = "invoice", "Invoice"
        PAYMENT = "payment", "Payment"

    school = models.ForeignKey(
        "School",
        on_delete=models.CASCADE,
        related_name="billing_audit_logs",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices, db_index=True)
    summary = models.CharField(max_length=512)
    payload = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="school_billing_audit_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.school_id} {self.kind} @ {self.created_at}"


class SchoolGeneratedInvoice(models.Model):
    """
    SaaS invoices generated from the Super Admin Control Center (snapshot + GST).
    Distinct from legacy ``PlatformInvoice`` (calendar month rows).
    """

    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    school = models.ForeignKey(
        "School",
        on_delete=models.CASCADE,
        related_name="generated_invoices",
    )
    invoice_number = models.CharField(max_length=64, unique=True, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ISSUED,
        db_index=True,
    )
    include_gst = models.BooleanField(default=False)
    gst_rate_percent = models.DecimalField(max_digits=5, decimal_places=2, default=18)
    student_count = models.PositiveIntegerField(default=0, help_text="Headcount used on invoice.")
    tenant_student_count = models.PositiveIntegerField(
        default=0,
        help_text="Live tenant student count at generation time.",
    )
    plan_label = models.CharField(max_length=120, blank=True)
    plan_price_per_student = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    base_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    extra_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    concession_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Discount magnitude (positive rupees).",
    )
    subtotal_before_gst = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    snapshot = models.JSONField(default=dict, blank=True)
    billing_period_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Calendar year of the service month/year this invoice covers.",
    )
    billing_period_month = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="1–12 for monthly cycle; null for yearly (see invoice_month_key).",
    )
    invoice_month_key = models.CharField(
        max_length=8,
        blank=True,
        default="",
        db_index=True,
        help_text="Stable period id, e.g. 2026-05 or 2026-00 for annual.",
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Payment due date for tracking overdue vs invoice period (independent of paid_at).",
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_school_generated_invoices",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return self.invoice_number


class SchoolFeatureAddon(models.Model):
    """
    Enable a super_admin feature for a school beyond their base plan, with a
    recorded extra monthly charge (billing is tracked here; invoicing may be manual).
    """

    school = models.ForeignKey(
        "School",
        on_delete=models.CASCADE,
        related_name="feature_addons",
    )
    feature = models.ForeignKey(
        "super_admin.Feature",
        on_delete=models.CASCADE,
        related_name="school_feature_addons",
    )
    extra_monthly_charge = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Additional INR per month agreed for this feature (0 if bundled gratis).",
    )
    notes = models.TextField(
        blank=True,
        help_text="Invoice ref, agreement, or internal note.",
    )
    is_enabled = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["school_id", "feature__category", "feature__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "feature"],
                name="customers_schoolfeatureaddon_unique_school_feature",
            )
        ]

    def __str__(self) -> str:
        return f"{self.school.code} + {self.feature.code}"


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
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="When false, this subscription row is ignored for feature access (paused or superseded).",
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
    school_generated_invoice = models.ForeignKey(
        "SchoolGeneratedInvoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_subscription_payments",
        help_text="When set, this receipt row was created from Control Center generated invoice payment.",
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
