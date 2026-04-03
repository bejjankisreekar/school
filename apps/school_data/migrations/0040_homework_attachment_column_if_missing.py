"""
Idempotent DDL: add school_data_homework.attachment when the row exists in
django_migrations for 0037 but the column was never created (tenant drift).

Safe if the column already exists (PostgreSQL IF NOT EXISTS).
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0039_examsession_display_order_updated_at"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                'ALTER TABLE school_data_homework '
                "ADD COLUMN IF NOT EXISTS attachment varchar(100) NULL;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
