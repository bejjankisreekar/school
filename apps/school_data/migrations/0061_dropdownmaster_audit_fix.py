from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0060_dropdownmaster"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            BEGIN
              -- Rename audit fields to match apps.core.models.BaseModel
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='school_data_dropdownmaster' AND column_name='updated_by_id'
              ) THEN
                ALTER TABLE school_data_dropdownmaster RENAME COLUMN updated_by_id TO modified_by_id;
              END IF;

              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='school_data_dropdownmaster' AND column_name='updated_on'
              ) THEN
                ALTER TABLE school_data_dropdownmaster RENAME COLUMN updated_on TO modified_on;
              END IF;

              -- Ensure created_on exists (it should), and modified_on exists after rename.
              IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='school_data_dropdownmaster' AND column_name='modified_on'
              ) THEN
                ALTER TABLE school_data_dropdownmaster ADD COLUMN modified_on timestamp with time zone;
              END IF;
            END $$;
            """,
            reverse_sql="""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='school_data_dropdownmaster' AND column_name='modified_by_id'
              ) THEN
                ALTER TABLE school_data_dropdownmaster RENAME COLUMN modified_by_id TO updated_by_id;
              END IF;
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='school_data_dropdownmaster' AND column_name='modified_on'
              ) THEN
                ALTER TABLE school_data_dropdownmaster RENAME COLUMN modified_on TO updated_on;
              END IF;
            END $$;
            """,
        ),
    ]

