import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_missing_profiles(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    UserProfile = apps.get_model("accounts", "UserProfile")
    for u in User.objects.all().iterator():
        UserProfile.objects.get_or_create(user_id=u.pk)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_user_email_required_non_unique"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "avatar",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="user_avatars/%Y/%m/",
                        validators=[
                            django.core.validators.FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"])
                        ],
                    ),
                ),
                ("middle_name", models.CharField(blank=True, max_length=150)),
                ("display_name", models.CharField(blank=True, help_text="Shown in the app header; defaults to your full name if empty.", max_length=255)),
                ("date_of_birth", models.DateField(blank=True, null=True)),
                (
                    "gender",
                    models.CharField(
                        blank=True,
                        choices=[("M", "Male"), ("F", "Female"), ("O", "Other"), ("", "Prefer not to say")],
                        default="",
                        max_length=1,
                    ),
                ),
                (
                    "marital_status",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("SINGLE", "Single"),
                            ("MARRIED", "Married"),
                            ("DIVORCED", "Divorced"),
                            ("WIDOWED", "Widowed"),
                            ("OTHER", "Other"),
                            ("", "Unspecified"),
                        ],
                        default="",
                        max_length=20,
                    ),
                ),
                (
                    "blood_group",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("A+", "A+"),
                            ("A-", "A-"),
                            ("B+", "B+"),
                            ("B-", "B-"),
                            ("AB+", "AB+"),
                            ("AB-", "AB-"),
                            ("O+", "O+"),
                            ("O-", "O-"),
                            ("", "Unknown"),
                        ],
                        default="",
                        max_length=4,
                    ),
                ),
                ("nationality", models.CharField(blank=True, max_length=120)),
                (
                    "aadhaar_or_govt_id",
                    models.CharField(
                        blank=True,
                        help_text="Internal use only; restrict visibility in your security policy.",
                        max_length=32,
                    ),
                ),
                ("emergency_contact_name", models.CharField(blank=True, max_length=255)),
                ("emergency_contact_phone", models.CharField(blank=True, max_length=32)),
                ("secondary_email", models.EmailField(blank=True, max_length=254)),
                ("alternate_phone", models.CharField(blank=True, max_length=32)),
                ("whatsapp_number", models.CharField(blank=True, max_length=32)),
                ("address_line1", models.CharField(blank=True, max_length=255)),
                ("address_line2", models.CharField(blank=True, max_length=255)),
                ("city", models.CharField(blank=True, max_length=120)),
                ("state", models.CharField(blank=True, max_length=120)),
                ("pin_code", models.CharField(blank=True, max_length=16)),
                ("country", models.CharField(blank=True, default="", max_length=120)),
                ("department", models.CharField(blank=True, max_length=120)),
                ("designation", models.CharField(blank=True, max_length=120)),
                ("branch", models.CharField(blank=True, max_length=120)),
                ("employee_code", models.CharField(blank=True, max_length=64)),
                ("official_email", models.EmailField(blank=True, max_length=254)),
                ("language", models.CharField(blank=True, default="en", max_length=32)),
                ("timezone", models.CharField(blank=True, default="Asia/Kolkata", max_length=64)),
                (
                    "theme",
                    models.CharField(
                        choices=[("SYSTEM", "Match system"), ("LIGHT", "Light"), ("DARK", "Dark")],
                        default="SYSTEM",
                        max_length=16,
                    ),
                ),
                ("notify_in_app", models.BooleanField(default=True)),
                ("notify_email", models.BooleanField(default=True)),
                ("notify_sms", models.BooleanField(default=False)),
                ("two_factor_enabled", models.BooleanField(default=False)),
                ("password_changed_at", models.DateTimeField(blank=True, null=True)),
                ("profile_updated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "profile_updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="profiles_last_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reporting_manager",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="direct_reports_profiles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "user profile",
                "verbose_name_plural": "user profiles",
            },
        ),
        migrations.RunPython(create_missing_profiles, noop_reverse),
    ]
