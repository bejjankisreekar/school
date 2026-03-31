from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("school_data", "0029_classroom_active_schedule_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="extra_data",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Flexible admission/profile fields (course/branch, documents metadata, medical, billing preferences, etc.).",
            ),
        ),
    ]

