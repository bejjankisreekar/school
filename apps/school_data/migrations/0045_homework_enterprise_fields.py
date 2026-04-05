import django.db.models.deletion
from django.db import migrations, models


def backfill_homework_assigned_date(apps, schema_editor):
    Homework = apps.get_model("school_data", "Homework")
    for hw in Homework.objects.filter(assigned_date__isnull=True).iterator(chunk_size=500):
        if hw.created_at:
            Homework.objects.filter(pk=hw.pk).update(assigned_date=hw.created_at.date())


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0044_examsession_modified_columns_if_missing"),
    ]

    operations = [
        migrations.AddField(
            model_name="homework",
            name="assigned_date",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="homework",
            name="homework_type",
            field=models.CharField(
                choices=[
                    ("CLASSWORK", "Classwork"),
                    ("HOMEWORK", "Homework"),
                    ("PROJECT", "Project"),
                    ("ASSIGNMENT", "Assignment"),
                    ("REVISION", "Revision work"),
                    ("LAB", "Lab work"),
                    ("READING", "Reading task"),
                ],
                db_index=True,
                default="HOMEWORK",
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="homework",
            name="max_marks",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="homework",
            name="submission_type",
            field=models.CharField(
                choices=[
                    ("NOTEBOOK", "Notebook submission"),
                    ("ONLINE", "Online upload"),
                    ("BOTH", "Both"),
                    ("ORAL", "Oral / practical"),
                ],
                default="NOTEBOOK",
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="homework",
            name="allow_late_submission",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="homework",
            name="late_submission_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="homework",
            name="priority",
            field=models.CharField(
                choices=[
                    ("LOW", "Low"),
                    ("NORMAL", "Normal"),
                    ("HIGH", "High"),
                    ("URGENT", "Urgent"),
                ],
                db_index=True,
                default="NORMAL",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="homework",
            name="status",
            field=models.CharField(
                choices=[
                    ("DRAFT", "Draft"),
                    ("PUBLISHED", "Published"),
                    ("CLOSED", "Closed"),
                    ("ARCHIVED", "Archived"),
                ],
                db_index=True,
                default="PUBLISHED",
                help_text="Draft is hidden from students; published and closed are visible; archived is hidden.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="homework",
            name="academic_year",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="homeworks",
                to="school_data.academicyear",
            ),
        ),
        migrations.AddField(
            model_name="homework",
            name="estimated_duration_minutes",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="homework",
            name="instructions",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="homework",
            name="submission_required",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(backfill_homework_assigned_date, migrations.RunPython.noop),
    ]
