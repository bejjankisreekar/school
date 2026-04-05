"""
Add Homework enterprise columns when migration 0045 did not run on a tenant (schema drift).

Uses PostgreSQL IF NOT EXISTS. Safe if columns already exist.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0045_homework_enterprise_fields"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            ALTER TABLE school_data_homework
                ADD COLUMN IF NOT EXISTS assigned_date date NULL;
            ALTER TABLE school_data_homework
                ADD COLUMN IF NOT EXISTS homework_type varchar(50) NOT NULL DEFAULT 'HOMEWORK';
            ALTER TABLE school_data_homework
                ADD COLUMN IF NOT EXISTS max_marks smallint NULL;
            ALTER TABLE school_data_homework
                ADD COLUMN IF NOT EXISTS submission_type varchar(50) NOT NULL DEFAULT 'NOTEBOOK';
            ALTER TABLE school_data_homework
                ADD COLUMN IF NOT EXISTS allow_late_submission boolean NOT NULL DEFAULT false;
            ALTER TABLE school_data_homework
                ADD COLUMN IF NOT EXISTS late_submission_until timestamp with time zone NULL;
            ALTER TABLE school_data_homework
                ADD COLUMN IF NOT EXISTS priority varchar(20) NOT NULL DEFAULT 'NORMAL';
            ALTER TABLE school_data_homework
                ADD COLUMN IF NOT EXISTS status varchar(20) NOT NULL DEFAULT 'PUBLISHED';
            ALTER TABLE school_data_homework
                ADD COLUMN IF NOT EXISTS academic_year_id bigint NULL;
            ALTER TABLE school_data_homework
                ADD COLUMN IF NOT EXISTS estimated_duration_minutes integer NULL;
            ALTER TABLE school_data_homework
                ADD COLUMN IF NOT EXISTS instructions text NOT NULL DEFAULT '';
            ALTER TABLE school_data_homework
                ADD COLUMN IF NOT EXISTS submission_required boolean NOT NULL DEFAULT true;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
