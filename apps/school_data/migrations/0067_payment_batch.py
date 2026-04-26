# Generated manually for multi-line fee payments

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0066_classroom_grade_order"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("total_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("payment_date", models.DateField()),
                ("payment_method", models.CharField(default="Cash", max_length=50)),
                ("receipt_number", models.CharField(blank=True, help_text="School receipt / voucher number (optional).", max_length=50)),
                (
                    "transaction_reference",
                    models.CharField(blank=True, help_text="UPI ref., bank ref., or online payment id (optional).", max_length=120),
                ),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "academic_year",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payment_batches",
                        to="school_data.academicyear",
                    ),
                ),
                (
                    "received_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payment_batches_received",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payment_batches", to="school_data.student"),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddField(
            model_name="payment",
            name="batch",
            field=models.ForeignKey(
                blank=True,
                help_text="When set, this row is part of a multi-line payment.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="line_payments",
                to="school_data.paymentbatch",
            ),
        ),
    ]
