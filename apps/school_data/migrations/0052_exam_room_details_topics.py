# Exam.room, details, topics were added on the model without a migration.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0051_holiday_calendar"),
    ]

    operations = [
        migrations.AddField(
            model_name="exam",
            name="room",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional: exam room / hall (e.g., Room 102, Main Hall).",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="exam",
            name="details",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional: instructions / syllabus / notes for this paper.",
            ),
        ),
        migrations.AddField(
            model_name="exam",
            name="topics",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional: topics covered (free text).",
            ),
        ),
    ]
