"""Re-apply homework enterprise DDL one statement at a time (avoids multi-statement execute issues)."""

from django.db import migrations

from apps.school_data.homework_schema_repair import ensure_homework_enterprise_columns


def _forward(apps, schema_editor):
    ensure_homework_enterprise_columns(schema_editor.connection)


def _backward(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0049_homework_enterprise_columns_if_missing_again"),
    ]

    operations = [
        migrations.RunPython(_forward, _backward),
    ]
