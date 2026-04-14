# Generated manually for academic year wizard

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0052_exam_room_details_topics"),
    ]

    operations = [
        migrations.AddField(
            model_name="academicyear",
            name="description",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Internal notes for staff (not shown on student-facing screens by default).",
            ),
        ),
        migrations.AddField(
            model_name="academicyear",
            name="wizard_settings",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Structured setup from the academic year wizard (terms, working days, copy flags, etc.).",
            ),
        ),
    ]
