from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0033_teacher_profile_extended"),
    ]

    operations = [
        migrations.AddField(
            model_name="feestructure",
            name="due_day_of_month",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Typical due day of month (1–28) for this fee.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="feestructure",
            name="frequency",
            field=models.CharField(
                choices=[
                    ("MONTHLY", "Monthly"),
                    ("QUARTERLY", "Quarterly"),
                    ("SEMESTER", "Semester"),
                    ("YEARLY", "Yearly"),
                    ("ONE_TIME", "One-time"),
                ],
                default="MONTHLY",
                help_text="Billing cycle for display and planning.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="feestructure",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
    ]
