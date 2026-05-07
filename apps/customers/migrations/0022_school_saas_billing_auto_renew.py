from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0021_billing_audit_generated_invoice"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="saas_billing_auto_renew",
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text="When enabled, the school is treated as opting into automatic renewal for SaaS billing workflows.",
            ),
        ),
    ]
