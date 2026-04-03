"""
Ensure Exam.session_id exists for tenants where 0022 did not fully apply
(annotate Count(papers) fails without this column).
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0041_examsession_columns_if_missing"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE school_data_exam
                ADD COLUMN IF NOT EXISTS session_id bigint NULL;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
