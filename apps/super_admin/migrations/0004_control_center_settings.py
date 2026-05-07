from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("super_admin", "0003_plan_list_price_and_defaults"),
    ]

    operations = [
        migrations.CreateModel(
            name="ControlCenterSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("platform_name", models.CharField(default="Campus ERP", max_length=120)),
                ("logo", models.ImageField(blank=True, null=True, upload_to="control_center/")),
                ("default_language", models.CharField(default="en", max_length=10)),
                ("timezone", models.CharField(default="Asia/Kolkata", max_length=64)),
                (
                    "default_theme",
                    models.CharField(
                        choices=[("light", "Light"), ("dark", "Dark")],
                        default="light",
                        help_text="Default Control Center appearance for new sessions (browser still wins if user toggled).",
                        max_length=16,
                    ),
                ),
                (
                    "default_billing_cycle",
                    models.CharField(
                        choices=[("monthly", "Monthly"), ("yearly", "Yearly")],
                        default="monthly",
                        max_length=16,
                    ),
                ),
                (
                    "grace_period_days",
                    models.PositiveSmallIntegerField(
                        default=14,
                        help_text="Days after due before treating as overdue in UI copy (informational).",
                    ),
                ),
                ("gst_enabled", models.BooleanField(default=True)),
                ("gst_percent", models.DecimalField(decimal_places=2, default=Decimal("18"), max_digits=5)),
                ("currency_code", models.CharField(default="INR", max_length=8)),
                ("extra_charges_enabled", models.BooleanField(default=True)),
                ("concession_enabled", models.BooleanField(default=True)),
                ("email_notifications", models.BooleanField(default=True)),
                ("sms_notifications", models.BooleanField(default=False)),
                ("template_payment_reminder", models.TextField(blank=True, default="")),
                ("template_invoice", models.TextField(blank=True, default="")),
                ("template_welcome", models.TextField(blank=True, default="")),
                ("admin_session_timeout_minutes", models.PositiveSmallIntegerField(default=60)),
                ("password_min_length", models.PositiveSmallIntegerField(default=8)),
                ("password_require_special", models.BooleanField(default=False)),
                ("enable_two_factor", models.BooleanField(default=False)),
                ("auto_invoice_generation", models.BooleanField(default=False)),
                ("invoice_generation_day", models.PositiveSmallIntegerField(default=1)),
                (
                    "auto_mark_overdue_days",
                    models.PositiveSmallIntegerField(
                        default=14,
                        help_text="Issued invoices past due by this many days are treated as overdue in billing UIs.",
                    ),
                ),
                ("razorpay_key_id", models.CharField(blank=True, default="", max_length=120)),
                ("razorpay_key_secret", models.CharField(blank=True, default="", max_length=200)),
                ("twilio_account_sid", models.CharField(blank=True, default="", max_length=80)),
                ("twilio_auth_token", models.CharField(blank=True, default="", max_length=120)),
                ("maintenance_mode", models.BooleanField(default=False)),
            ],
            options={
                "verbose_name": "Control Center settings",
                "verbose_name_plural": "Control Center settings",
            },
        ),
    ]
