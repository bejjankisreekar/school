from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0025_school_registration_and_billing_start"),
    ]

    operations = [
        migrations.AddField(
            model_name="saasplatformpayment",
            name="school_generated_invoice",
            field=models.ForeignKey(
                blank=True,
                help_text="When set, this receipt row was created from Control Center generated invoice payment.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="platform_subscription_payments",
                to="customers.schoolgeneratedinvoice",
            ),
        ),
    ]
