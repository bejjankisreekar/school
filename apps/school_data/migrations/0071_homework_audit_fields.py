from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("school_data", "0070_marks_component_marks"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=[
                        "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS created_by_id bigint NULL;",
                        "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS created_on timestamp with time zone NULL;",
                        "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS modified_by_id bigint NULL;",
                        "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS modified_on timestamp with time zone NULL;",
                        "CREATE INDEX IF NOT EXISTS school_data_homework_created_on_idx ON school_data_homework (created_on);",
                        # Add FK constraints if possible (safe if already exists)
                        """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'school_data_homework_created_by_id_fk'
  ) THEN
    ALTER TABLE school_data_homework
      ADD CONSTRAINT school_data_homework_created_by_id_fk
      FOREIGN KEY (created_by_id) REFERENCES accounts_user(id) DEFERRABLE INITIALLY DEFERRED;
  END IF;
EXCEPTION WHEN undefined_table THEN
  -- accounts_user might not exist in some environments at this point; ignore
  NULL;
END $$;
""",
                        """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'school_data_homework_modified_by_id_fk'
  ) THEN
    ALTER TABLE school_data_homework
      ADD CONSTRAINT school_data_homework_modified_by_id_fk
      FOREIGN KEY (modified_by_id) REFERENCES accounts_user(id) DEFERRABLE INITIALLY DEFERRED;
  END IF;
EXCEPTION WHEN undefined_table THEN
  NULL;
END $$;
""",
                    ],
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="homework",
                    name="created_by",
                    field=models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="homework_created",
                        to="accounts.user",
                    ),
                ),
                migrations.AddField(
                    model_name="homework",
                    name="created_on",
                    field=models.DateTimeField(auto_now_add=True, db_index=True, editable=False, null=True),
                ),
                migrations.AddField(
                    model_name="homework",
                    name="modified_by",
                    field=models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="homework_modified",
                        to="accounts.user",
                    ),
                ),
                migrations.AddField(
                    model_name="homework",
                    name="modified_on",
                    field=models.DateTimeField(auto_now=True, editable=False, null=True),
                ),
            ],
        )
    ]

