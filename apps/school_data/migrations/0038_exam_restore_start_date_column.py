"""
Repair school_data_exam after buggy 0028 RemoveField(start_date) dropped the column.

Exam.date maps to DB column start_date; without it, every Exam query raises ProgrammingError.
Idempotent: no-op if start_date already exists (PostgreSQL / tenant schemas).
"""

from django.db import migrations


def _restore_start_date_column(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'school_data_exam'
            """
        )
        cols = {row[0] for row in cursor.fetchall()}
        if "start_date" in cols:
            return
        cursor.execute("ALTER TABLE school_data_exam ADD COLUMN start_date date NULL")
        if "end_date" in cols:
            cursor.execute(
                """
                UPDATE school_data_exam
                SET start_date = end_date
                WHERE start_date IS NULL AND end_date IS NOT NULL
                """
            )
        cursor.execute(
            "UPDATE school_data_exam SET start_date = CURRENT_DATE WHERE start_date IS NULL"
        )
        cursor.execute(
            "ALTER TABLE school_data_exam ALTER COLUMN start_date SET NOT NULL"
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS school_data_exam_start_date_idx
            ON school_data_exam (start_date)
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0037_homework_attachment"),
    ]

    operations = [
        migrations.RunPython(_restore_start_date_column, migrations.RunPython.noop),
    ]
