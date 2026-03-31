"""
Ensure legacy partial unique on is_active is gone from the database.

On PostgreSQL, Django may create this as a UNIQUE INDEX named unique_active_academic_year;
RemoveConstraint in 0031 may not remove it, so INSERT still hits duplicate key (is_active)=(t).
This migration drops index/constraint idempotently (safe to re-run).
"""

from django.db import migrations


def _drop_unique_active_year(apps, schema_editor):
    connection = schema_editor.connection
    vendor = connection.vendor
    if vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE school_data_academicyear DROP CONSTRAINT IF EXISTS unique_active_academic_year;"
            )
            cursor.execute("DROP INDEX IF EXISTS unique_active_academic_year;")
            cursor.execute(
                "DROP INDEX IF EXISTS school_data_academicyear_unique_active_academic_year;"
            )
    elif vendor == "sqlite":
        with connection.cursor() as cursor:
            cursor.execute("DROP INDEX IF EXISTS unique_active_academic_year;")


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0031_remove_academic_year_unique_active"),
    ]

    operations = [
        migrations.RunPython(_drop_unique_active_year, migrations.RunPython.noop),
    ]
