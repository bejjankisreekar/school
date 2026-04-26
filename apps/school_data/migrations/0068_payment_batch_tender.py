# Generated manually for split payment methods per receipt

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0067_payment_batch"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentBatchTender",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("payment_method", models.CharField(default="Cash", max_length=50)),
                (
                    "transaction_reference",
                    models.CharField(
                        blank=True,
                        help_text="Optional ref for this tender (UPI / cheque no. / etc.).",
                        max_length=120,
                    ),
                ),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tenders",
                        to="school_data.paymentbatch",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
            },
        ),
    ]
