# Generated manually for ScheduleProfile metadata fields

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0028_badge_alter_exam_options_alter_examsession_options_and_more"),
        ("timetable", "0005_scheduleprofile_alter_timetable_unique_together_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="scheduleprofile",
            options={"ordering": ["-is_active", "name"]},
        ),
        migrations.AddField(
            model_name="scheduleprofile",
            name="break_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Whether this profile typically includes break rows (hint for admins).",
            ),
        ),
        migrations.AddField(
            model_name="scheduleprofile",
            name="default_end_time",
            field=models.TimeField(
                blank=True,
                help_text="Suggested day end when generating or documenting this profile.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="scheduleprofile",
            name="default_start_time",
            field=models.TimeField(
                blank=True,
                help_text="Suggested day start when generating or documenting this profile.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="scheduleprofile",
            name="description",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="scheduleprofile",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="scheduleprofile",
            name="total_periods",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Target number of teaching periods (optional reference).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="scheduleprofile",
            name="academic_year",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="schedule_profiles",
                to="school_data.academicyear",
            ),
        ),
    ]
