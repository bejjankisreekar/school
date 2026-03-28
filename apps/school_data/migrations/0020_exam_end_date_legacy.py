"""
Initial Exam model had end_date NOT NULL; ORM did not set it → IntegrityError.

Backfill end_date from start_date, drop NOT NULL, then register end_date on the model.
"""

from django.db import migrations, models


def _fix_end_date_column(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'school_data_exam'
            """
        )
        cols = {row[0] for row in cursor.fetchall()}
        if "end_date" not in cols:
            return
        cursor.execute(
            """
            UPDATE school_data_exam
            SET end_date = start_date
            WHERE end_date IS NULL
            """
        )
        cursor.execute(
            """
            ALTER TABLE school_data_exam
            ALTER COLUMN end_date DROP NOT NULL
            """
        )


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0019_exam_date_maps_to_start_date"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(_fix_end_date_column, _noop_reverse),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="exam",
                    name="end_date",
                    field=models.DateField(
                        blank=True,
                        db_column="end_date",
                        help_text="Legacy column; defaults to the exam date on save.",
                        null=True,
                    ),
                ),
            ],
        ),
    ]
