from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0024_billing_period_and_free_service"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="registration_date",
            field=models.DateField(
                blank=True,
                help_text="School / contract registration anchor for billing configuration.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="school",
            name="billing_start_date",
            field=models.DateField(
                blank=True,
                help_text="First calendar day billable SaaS charges apply (defaults to day after free-until when set).",
                null=True,
            ),
        ),
    ]
