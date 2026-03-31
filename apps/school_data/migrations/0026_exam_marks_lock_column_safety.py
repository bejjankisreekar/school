"""Ensure column exists if a tenant DB was out of sync with migration state."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0025_exam_marks_teacher_edit_locked"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE school_data_exam "
                "ADD COLUMN IF NOT EXISTS marks_teacher_edit_locked boolean NOT NULL DEFAULT false;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
