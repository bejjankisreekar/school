# Exam session list: manual order + last updated

from django.db import migrations, models


def copy_created_to_updated(apps, schema_editor):
    ExamSession = apps.get_model("school_data", "ExamSession")
    for row in ExamSession.objects.all().iterator():
        if row.updated_at is None:
            ExamSession.objects.filter(pk=row.pk).update(updated_at=row.created_at)


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0038_exam_restore_start_date_column"),
    ]

    operations = [
        migrations.AddField(
            model_name="examsession",
            name="display_order",
            field=models.PositiveIntegerField(
                db_index=True,
                default=0,
                help_text="Manual card order (0 = use default date-based sort).",
            ),
        ),
        migrations.AddField(
            model_name="examsession",
            name="updated_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text="Updated when the session or its papers change.",
            ),
        ),
        migrations.RunPython(copy_created_to_updated, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="examsession",
            name="updated_at",
            field=models.DateTimeField(
                auto_now=True,
                help_text="Updated when the session or its papers change.",
            ),
        ),
    ]
