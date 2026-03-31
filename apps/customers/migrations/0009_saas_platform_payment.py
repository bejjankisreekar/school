# SaaS platform payment ledger (superadmin "receive payment")

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("customers", "0008_billing_plans_coupons_subscriptions"),
    ]

    operations = [
        migrations.CreateModel(
            name="SaaSPlatformPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("payment_date", models.DateField(db_index=True)),
                (
                    "payment_method",
                    models.CharField(
                        choices=[
                            ("upi", "UPI"),
                            ("bank_transfer", "Bank transfer (NEFT / RTGS / IMPS)"),
                            ("cash", "Cash"),
                            ("card", "Card / payment gateway"),
                            ("cheque", "Cheque"),
                            ("other", "Other"),
                        ],
                        default="upi",
                        max_length=30,
                    ),
                ),
                (
                    "reference",
                    models.CharField(
                        blank=True,
                        help_text="UTR, transaction id, cheque no., or receipt reference",
                        max_length=200,
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="recorded_saas_platform_payments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "school",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="saas_platform_payments",
                        to="customers.school",
                    ),
                ),
                (
                    "subscription",
                    models.ForeignKey(
                        blank=True,
                        help_text="Optional link to the subscription period this payment covers.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="platform_payments",
                        to="customers.schoolsubscription",
                    ),
                ),
            ],
            options={
                "verbose_name": "platform subscription payment",
                "verbose_name_plural": "platform subscription payments",
                "ordering": ["-payment_date", "-id"],
            },
        ),
    ]
