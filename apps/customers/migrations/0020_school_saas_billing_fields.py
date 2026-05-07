from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0019_schoolfeatureaddon"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="billing_extra_per_student_month",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Extra platform charge per student per month (added to plan list price).",
                max_digits=10,
            ),
        ),
        migrations.AddField(
            model_name="school",
            name="billing_concession_per_student_month",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Concession/discount per student per month on the platform bill.",
                max_digits=10,
            ),
        ),
        migrations.AddField(
            model_name="school",
            name="saas_billing_cycle",
            field=models.CharField(
                choices=[("monthly", "Monthly"), ("yearly", "Yearly")],
                db_index=True,
                default="monthly",
                help_text="Whether the school is invoiced on a monthly or yearly SaaS cycle.",
                max_length=16,
            ),
        ),
    ]
