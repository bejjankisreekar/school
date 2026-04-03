"""
Add ExamSession.display_order and updated_at when migration 0039 was not applied
correctly on a tenant (avoids ProgrammingError on /school/exams/).
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0040_homework_attachment_column_if_missing"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE school_data_examsession
                ADD COLUMN IF NOT EXISTS display_order integer NOT NULL DEFAULT 0;
            ALTER TABLE school_data_examsession
                ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NULL;
            UPDATE school_data_examsession
                SET updated_at = created_at
                WHERE updated_at IS NULL AND created_at IS NOT NULL;
            CREATE INDEX IF NOT EXISTS school_data_examsession_display_order_idx
                ON school_data_examsession (display_order);
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
