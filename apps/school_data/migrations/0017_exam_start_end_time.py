from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0016_alter_subject_code_max_length"),
    ]

    operations = [
        migrations.AddField(
            model_name="exam",
            name="start_time",
            field=models.TimeField(
                blank=True,
                help_text="Optional start time for calendar / timetable display.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="exam",
            name="end_time",
            field=models.TimeField(
                blank=True,
                help_text="Optional end time for calendar / timetable display.",
                null=True,
            ),
        ),
    ]
