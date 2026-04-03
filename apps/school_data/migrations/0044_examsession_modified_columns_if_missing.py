"""
Add ExamSession.modified_by_id / modified_at when migration 0043 was not applied on a tenant.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0043_examsession_modified_audit"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE school_data_examsession
                ADD COLUMN IF NOT EXISTS modified_at timestamp with time zone NULL;
            ALTER TABLE school_data_examsession
                ADD COLUMN IF NOT EXISTS modified_by_id bigint NULL;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
