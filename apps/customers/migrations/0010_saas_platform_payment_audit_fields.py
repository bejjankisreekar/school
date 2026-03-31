# Optional audit fields for platform subscription payments

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0009_saas_platform_payment"),
    ]

    operations = [
        migrations.AddField(
            model_name="saasplatformpayment",
            name="internal_receipt_no",
            field=models.CharField(
                blank=True,
                help_text="Your internal voucher or receipt book number (for audits).",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="saasplatformpayment",
            name="service_period_start",
            field=models.DateField(
                blank=True,
                help_text="Optional: subscription or service period this payment covers (start).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="saasplatformpayment",
            name="service_period_end",
            field=models.DateField(
                blank=True,
                help_text="Optional: subscription or service period this payment covers (end).",
                null=True,
            ),
        ),
    ]
