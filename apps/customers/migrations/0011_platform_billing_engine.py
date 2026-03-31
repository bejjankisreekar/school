# Platform SaaS billing: invoices, invoice payments, receipts (public schema)

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("customers", "0010_saas_platform_payment_audit_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformInvoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("month", models.PositiveSmallIntegerField(help_text="1–12")),
                ("year", models.PositiveSmallIntegerField()),
                ("students_count", models.PositiveIntegerField(default=0)),
                ("price_per_student", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("gross_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("discount_amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("final_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("partial", "Partial"),
                            ("paid", "Paid"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("due_date", models.DateField(db_index=True)),
                ("invoice_number", models.CharField(db_index=True, max_length=40, unique=True)),
                ("created_on", models.DateTimeField(auto_now_add=True)),
                (
                    "school",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="platform_invoices",
                        to="customers.school",
                    ),
                ),
                (
                    "subscription",
                    models.ForeignKey(
                        blank=True,
                        help_text="Subscription row this invoice was generated from.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="platform_invoices",
                        to="customers.schoolsubscription",
                    ),
                ),
            ],
            options={
                "db_table": "saas_invoices",
                "ordering": ["-year", "-month", "-id"],
            },
        ),
        migrations.CreateModel(
            name="PlatformInvoicePayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount_paid", models.DecimalField(decimal_places=2, max_digits=12)),
                (
                    "payment_mode",
                    models.CharField(
                        choices=[("upi", "UPI"), ("cash", "Cash"), ("bank", "Bank")],
                        max_length=20,
                    ),
                ),
                ("transaction_id", models.CharField(blank=True, max_length=200)),
                ("paid_on", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "invoice",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="invoice_payments",
                        to="customers.platforminvoice",
                    ),
                ),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="recorded_platform_invoice_payments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "school",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="platform_invoice_payments",
                        to="customers.school",
                    ),
                ),
            ],
            options={
                "db_table": "saas_invoice_payments",
                "ordering": ["-paid_on", "-id"],
            },
        ),
        migrations.CreateModel(
            name="PlatformBillingReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("receipt_number", models.CharField(db_index=True, max_length=40, unique=True)),
                (
                    "pdf_url",
                    models.CharField(
                        blank=True,
                        help_text="Path under MEDIA_ROOT or storage-relative URL key.",
                        max_length=500,
                    ),
                ),
                ("generated_on", models.DateTimeField(auto_now_add=True)),
                (
                    "payment",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="billing_receipt",
                        to="customers.platforminvoicepayment",
                    ),
                ),
            ],
            options={
                "db_table": "saas_billing_receipts",
                "ordering": ["-generated_on"],
            },
        ),
        migrations.AddConstraint(
            model_name="platforminvoice",
            constraint=models.UniqueConstraint(
                fields=("school", "year", "month"),
                name="uniq_saas_invoice_school_period",
            ),
        ),
        migrations.AddIndex(
            model_name="platforminvoice",
            index=models.Index(fields=["year", "month"], name="saas_inv_year_month_idx"),
        ),
    ]
