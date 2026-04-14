"""
Core shared models for School ERP. Lives in public schema.
BaseModel and Plan are used by both public and tenant apps.
"""
from django.db import models


class BaseModel(models.Model):
    """
    Abstract base model for audit tracking.
    All auditable models inherit from this to store creation and modification metadata.
    """
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(class)s_created",
        editable=False,
    )
    created_on = models.DateTimeField(auto_now_add=True, editable=False, db_index=True)
    modified_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(class)s_modified",
        editable=False,
    )
    modified_on = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True

    def save_with_audit(self, user, *args, **kwargs):
        """Save instance with audit fields set."""
        if not self.pk:
            self.created_by = user
        self.modified_by = user
        self.save(*args, **kwargs)


class Plan(models.Model):
    """Subscription plan: Basic, Pro, Enterprise. Gates feature access."""
    class PlanType(models.TextChoices):
        BASIC = "BASIC", "Basic"
        PRO = "PRO", "Pro"
        ENTERPRISE = "ENTERPRISE", "Enterprise"

    plan_type = models.CharField(max_length=20, choices=PlanType.choices, unique=True)
    name = models.CharField(max_length=100)
    enabled_modules = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return self.name

    PRO_MODULES = frozenset((
        "online_admissions", "online_results", "topper_list", "custom_branding",
        "ai_marksheet_summaries", "library", "hostel", "transport", "api_access", "priority_support",
    ))

    def has_module(self, module: str) -> bool:
        """Check if plan has access to a module."""
        if self.plan_type in (self.PlanType.PRO, self.PlanType.ENTERPRISE):
            if module in self.PRO_MODULES:
                return True
            return True
        mods = self.enabled_modules if isinstance(self.enabled_modules, (list, dict)) else []
        if isinstance(mods, dict):
            mods = mods.get("modules", [])
        if not mods and self.plan_type == self.PlanType.BASIC:
            return True
        return module in mods or not mods


class SidebarMenuItem(BaseModel):
    """
    Public-schema sidebar menu configuration (managed by Super Admin).

    Rendered for tenant users based on their role. Parent/child relationships
    enable nested submenus; ordering is per role + parent.
    """

    class Role(models.TextChoices):
        SUPERADMIN = "SUPERADMIN", "Super Admin"
        ADMIN = "ADMIN", "School Admin"
        TEACHER = "TEACHER", "Teacher"
        STUDENT = "STUDENT", "Student"
        PARENT = "PARENT", "Parent"

    role = models.CharField(max_length=20, choices=Role.choices, db_index=True)
    label = models.CharField(max_length=80)
    route_name = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Django URL name, e.g. core:school_students_list",
    )
    href = models.CharField(
        max_length=240,
        blank=True,
        default="",
        help_text="Optional fallback URL if route_name is empty or cannot be reversed.",
    )
    icon = models.CharField(
        max_length=60,
        blank=True,
        default="",
        help_text='Bootstrap icon class, e.g. "bi bi-people".',
    )
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    is_visible = models.BooleanField(default=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    feature_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Optional feature gate code (matches has_feature_access).",
    )

    class Meta:
        ordering = ["role", "parent_id", "display_order", "id"]
        indexes = [
            models.Index(fields=["role", "parent", "display_order"]),
        ]

    def __str__(self) -> str:
        return f"{self.role}: {self.label}"


class SubscriptionPlan(models.Model):
    """
    Subscription plan with pricing and features.
    Example: Basic Plan (₹5350/year), Pro Plan (₹9999+/year).
    """
    class BillingCycle(models.TextChoices):
        MONTHLY = "MONTHLY", "Monthly"
        QUARTERLY = "QUARTERLY", "Quarterly"
        YEARLY = "YEARLY", "Yearly"

    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Price in INR")
    billing_cycle = models.CharField(
        max_length=20,
        choices=BillingCycle.choices,
        default=BillingCycle.YEARLY,
    )
    description = models.TextField(blank=True)
    features = models.JSONField(
        default=list,
        blank=True,
        help_text="List of feature strings, e.g. ['online_admissions', 'library']",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["price"]
        verbose_name = "Subscription Plan"
        verbose_name_plural = "Subscription Plans"

    def __str__(self) -> str:
        return f"{self.name} (₹{self.price}/{self.billing_cycle})"


class SchoolSubscription(models.Model):
    """Links a school to a subscription plan with dates and trial flag."""
    school = models.ForeignKey(
        "customers.School",
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="school_subscriptions",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    is_trial = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "School Subscription"
        verbose_name_plural = "School Subscriptions"

    def __str__(self) -> str:
        return f"{self.school.name} → {self.plan.name} ({self.start_date} to {self.end_date})"


class ContactEnquiry(models.Model):
    """
    Public contact form enquiries (stored in the shared/public schema).

    Used by:
    - Marketing contact page: /contact/
    - Super admin tracking: /superadmin/enquiries/
    """

    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True, null=True)
    school_name = models.CharField(max_length=255, blank=True, null=True)
    message = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Enquiry from {self.name} ({self.email})"


class SchoolEnrollmentRequest(models.Model):
    """
    Public signup: a school requests to be onboarded. Stored in public schema.
    Super admin provisions a tenant (PostgreSQL schema + migrations) from the UI.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending review"
        PROVISIONED = "PROVISIONED", "Tenant created"
        DECLINED = "DECLINED", "Declined"

    institution_name = models.CharField(max_length=255)
    institution_code = models.CharField(
        max_length=100,
        blank=True,
        help_text="Short code or abbreviation for the school (optional).",
    )
    contact_name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    pincode = models.CharField(max_length=20, blank=True)
    student_count = models.PositiveIntegerField(null=True, blank=True)
    teacher_count = models.PositiveIntegerField(null=True, blank=True)
    branch_count = models.PositiveIntegerField(null=True, blank=True)
    preferred_username = models.CharField(max_length=150, blank=True)
    pending_password_hash = models.CharField(max_length=128, blank=True)
    intended_plan = models.CharField(
        max_length=32,
        blank=True,
        help_text="trial, basic, standard, enterprise, yearly — post-trial billing preference (audit).",
    )
    notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    school = models.ForeignKey(
        "customers.School",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollment_requests",
    )
    provisioned_schema_name = models.CharField(max_length=63, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_enrollments",
    )
    decline_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.institution_name} ({self.email})"
