# Platform-wide control center metadata (limits, duration, login lock, role permission drafts).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0016_school_timetable_current_profile_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="platform_control_meta",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Super Admin Control Center: limits, plan duration, disable_login, role_permissions JSON, etc.",
            ),
        ),
    ]
