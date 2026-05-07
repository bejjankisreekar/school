from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0022_school_saas_billing_auto_renew"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="saas_billing_complimentary_until",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Through this date (inclusive), Control Center invoices may be issued at ₹0 as a complimentary period.",
            ),
        ),
    ]
