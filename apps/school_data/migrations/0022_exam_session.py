import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0021_exam_classroom_nullable"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExamSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("class_name", models.CharField(db_index=True, max_length=50)),
                ("section", models.CharField(db_index=True, max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "classroom",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="exam_sessions",
                        to="school_data.classroom",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="exam_sessions_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddField(
            model_name="exam",
            name="session",
            field=models.ForeignKey(
                blank=True,
                help_text="When set, this row is a subject paper under a multi-subject exam session.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="papers",
                to="school_data.examsession",
            ),
        ),
    ]
