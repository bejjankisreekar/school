# Generated manually for Homework class/section assignment

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def populate_assigned_by(apps, schema_editor):
    """Set assigned_by from teacher.user for existing homework."""
    Homework = apps.get_model("school_data", "Homework")
    for hw in Homework.objects.filter(assigned_by__isnull=True):
        if hw.teacher_id:
            hw.assigned_by_id = hw.teacher.user_id
            hw.save(update_fields=["assigned_by_id"])


def reverse_populate(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0011_homeworksubmission"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="homework",
            name="assigned_by",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="assigned_homework",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="homework",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, db_index=True, default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name="homework",
            name="classes",
            field=models.ManyToManyField(
                blank=True,
                related_name="homeworks",
                to="school_data.classroom",
            ),
        ),
        migrations.AddField(
            model_name="homework",
            name="sections",
            field=models.ManyToManyField(
                blank=True,
                related_name="homeworks",
                to="school_data.section",
            ),
        ),
        migrations.AlterField(
            model_name="homework",
            name="subject",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="homeworks",
                to="school_data.subject",
            ),
        ),
        migrations.AlterField(
            model_name="homework",
            name="teacher",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="homeworks",
                to="school_data.teacher",
            ),
        ),
        migrations.AlterField(
            model_name="homework",
            name="due_date",
            field=models.DateField(db_index=True),
        ),
        migrations.RunPython(populate_assigned_by, reverse_populate),
        migrations.AlterModelOptions(
            name="homework",
            options={"ordering": ["-due_date", "-created_at"]},
        ),
    ]
