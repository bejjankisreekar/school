import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0042_exam_session_id_column_if_missing"),
    ]

    operations = [
        migrations.AddField(
            model_name="examsession",
            name="modified_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When session metadata was last edited by an admin.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="examsession",
            name="modified_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Last school admin who changed this session (name/class/section, etc.).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="exam_sessions_modified",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
