"""
FeeType description + is_active. Idempotent SQL for tenant schemas where columns
already exist (migration history drift).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0035_fee_concession_fields"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="feetype",
                    name="description",
                    field=models.TextField(blank=True, default=""),
                ),
                migrations.AddField(
                    model_name="feetype",
                    name="is_active",
                    field=models.BooleanField(db_index=True, default=True),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    ALTER TABLE school_data_feetype
                        ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '';
                    ALTER TABLE school_data_feetype
                        ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
                    CREATE INDEX IF NOT EXISTS school_data_feetype_is_active
                        ON school_data_feetype (is_active);
                    """,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
