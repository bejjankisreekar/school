"""
Legacy Exam rows had classroom_id NOT NULL; the app uses class_name + section strings.

Drop NOT NULL so rows can rely on denormalized names; new creates set classroom FK explicitly.
"""

from django.db import migrations, models
import django.db.models.deletion


def _classroom_id_drop_not_null(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'school_data_exam'
              AND column_name = 'classroom_id'
            """
        )
        if not cursor.fetchone():
            return
        cursor.execute(
            """
            ALTER TABLE school_data_exam
            ALTER COLUMN classroom_id DROP NOT NULL
            """
        )


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0020_exam_end_date_legacy"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(_classroom_id_drop_not_null, _noop_reverse),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="exam",
                    name="classroom",
                    field=models.ForeignKey(
                        blank=True,
                        db_column="classroom_id",
                        help_text="Legacy column; set from class when saving if missing.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="exams",
                        to="school_data.classroom",
                    ),
                ),
            ],
        ),
    ]
