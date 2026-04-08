from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0015_school_payslip_format"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="timetable_current_profile_id",
            field=models.BigIntegerField(
                blank=True,
                help_text="ScheduleProfile id to treat as the currently published timetable (optional).",
                null=True,
            ),
        ),
    ]

