"""
Legacy DB used `start_date` (NOT NULL); newer code added a separate `date` column.
ORM inserts went to `date`, leaving `start_date` null → IntegrityError.

This migration copies `date` → `start_date` when both exist, drops duplicate `date`,
and updates Django state so `Exam.date` maps to column `start_date`.
"""

from django.db import migrations, models


def _sync_exam_date_columns(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'school_data_exam'
            """
        )
        cols = {row[0] for row in cursor.fetchall()}
        if "date" in cols and "start_date" in cols:
            cursor.execute(
                """
                UPDATE school_data_exam
                SET start_date = COALESCE(start_date, date)
                WHERE date IS NOT NULL
                """
            )
            cursor.execute('ALTER TABLE school_data_exam DROP COLUMN "date"')
        elif "date" in cols and "start_date" not in cols:
            cursor.execute('ALTER TABLE school_data_exam RENAME COLUMN "date" TO start_date')


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0018_class_section_subject_teacher"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(_sync_exam_date_columns, _noop_reverse),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="exam",
                    name="date",
                    field=models.DateField(db_index=True, db_column="start_date"),
                ),
            ],
        ),
    ]
