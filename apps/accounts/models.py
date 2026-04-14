from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    class Roles(models.TextChoices):
        SUPERADMIN = "SUPERADMIN", "Super admin"
        ADMIN = "ADMIN", "School admin"
        TEACHER = "TEACHER", "Teacher"
        STUDENT = "STUDENT", "Student"
        PARENT = "PARENT", "Parent"

    role = models.CharField(
        max_length=20,
        choices=Roles.choices,
        default=Roles.STUDENT,
    )
    school = models.ForeignKey(
        "customers.School",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        db_index=True,
        to_field="code",
    )
    email = models.EmailField(
        null=False,
        blank=False,
        help_text="Required. Can be shared (e.g. parent email for multiple students).",
    )
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        help_text="Optional contact phone number.",
    )
    is_first_login = models.BooleanField(
        default=False,
        help_text="If True, user must change password on next login.",
    )

    def __str__(self) -> str:
        return f"{self.username} ({self.get_role_display()})"


class UserProfile(models.Model):
    """Extended profile and preferences for ERP users (staff, parents, students)."""

    class Gender(models.TextChoices):
        MALE = "M", _("Male")
        FEMALE = "F", _("Female")
        OTHER = "O", _("Other")
        UNSPECIFIED = "", _("Prefer not to say")

    class MaritalStatus(models.TextChoices):
        SINGLE = "SINGLE", _("Single")
        MARRIED = "MARRIED", _("Married")
        DIVORCED = "DIVORCED", _("Divorced")
        WIDOWED = "WIDOWED", _("Widowed")
        OTHER = "OTHER", _("Other")
        UNSPECIFIED = "", _("Unspecified")

    class BloodGroup(models.TextChoices):
        A_POS = "A+", "A+"
        A_NEG = "A-", "A-"
        B_POS = "B+", "B+"
        B_NEG = "B-", "B-"
        AB_POS = "AB+", "AB+"
        AB_NEG = "AB-", "AB-"
        O_POS = "O+", "O+"
        O_NEG = "O-", "O-"
        UNKNOWN = "", _("Unknown")

    class ThemePreference(models.TextChoices):
        SYSTEM = "SYSTEM", _("Match system")
        LIGHT = "LIGHT", _("Light")
        DARK = "DARK", _("Dark")

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    avatar = models.ImageField(
        upload_to="user_avatars/%Y/%m/",
        blank=True,
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"])],
    )
    middle_name = models.CharField(max_length=150, blank=True)
    display_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Shown in the app header; defaults to your full name if empty.",
    )
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(
        max_length=1,
        choices=Gender.choices,
        blank=True,
        default=Gender.UNSPECIFIED,
    )
    marital_status = models.CharField(
        max_length=20,
        choices=MaritalStatus.choices,
        blank=True,
        default=MaritalStatus.UNSPECIFIED,
    )
    blood_group = models.CharField(
        max_length=4,
        choices=BloodGroup.choices,
        blank=True,
        default=BloodGroup.UNKNOWN,
    )
    nationality = models.CharField(max_length=120, blank=True)
    aadhaar_or_govt_id = models.CharField(
        max_length=32,
        blank=True,
        help_text="Internal use only; restrict visibility in your security policy.",
    )
    emergency_contact_name = models.CharField(max_length=255, blank=True)
    emergency_contact_phone = models.CharField(max_length=32, blank=True)

    secondary_email = models.EmailField(blank=True)
    alternate_phone = models.CharField(max_length=32, blank=True)
    whatsapp_number = models.CharField(max_length=32, blank=True)
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    pin_code = models.CharField(max_length=16, blank=True)
    country = models.CharField(max_length=120, blank=True, default="")

    department = models.CharField(max_length=120, blank=True)
    designation = models.CharField(max_length=120, blank=True)
    branch = models.CharField(max_length=120, blank=True)
    reporting_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="direct_reports_profiles",
    )
    employee_code = models.CharField(max_length=64, blank=True)
    official_email = models.EmailField(blank=True)

    language = models.CharField(max_length=32, blank=True, default="en")
    timezone = models.CharField(max_length=64, blank=True, default="Asia/Kolkata")
    theme = models.CharField(
        max_length=16,
        choices=ThemePreference.choices,
        default=ThemePreference.SYSTEM,
    )
    notify_in_app = models.BooleanField(default=True)
    notify_email = models.BooleanField(default=True)
    notify_sms = models.BooleanField(default=False)

    two_factor_enabled = models.BooleanField(default=False)

    password_changed_at = models.DateTimeField(null=True, blank=True)
    profile_updated_at = models.DateTimeField(auto_now=True)
    profile_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="profiles_last_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "user profile"
        verbose_name_plural = "user profiles"

    def __str__(self) -> str:
        return f"Profile for {self.user.username}"

    @property
    def resolved_display_name(self) -> str:
        if self.display_name.strip():
            return self.display_name.strip()
        return self.user.get_full_name() or self.user.username


class BlockedLoginAttempt(models.Model):
    """Audit log for blocked logins (e.g. inactive teacher/student). Stored in public schema."""

    username = models.CharField(max_length=150, db_index=True)
    role = models.CharField(max_length=20, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    reason = models.CharField(max_length=120, db_index=True, default="inactive_account")
    attempted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    school = models.ForeignKey(
        "customers.School",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blocked_login_attempts",
        to_field="code",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blocked_login_attempts",
    )

    class Meta:
        ordering = ["-attempted_at", "-id"]

    def __str__(self) -> str:
        return f"Blocked login: {self.username} ({self.role})"
