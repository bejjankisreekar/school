"""
Apply the SaaS Plan migration (0006) via raw SQL.
Use when 'python manage.py migrate' fails due to other migrations.
Run: python manage.py apply_saas_plan_migration
"""
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.utils import ProgrammingError


class Command(BaseCommand):
    help = "Apply saas_plan_id and enabled_features_override columns to customers_school via raw SQL"

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            # Check if columns already exist
            cursor.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'customers_school'
                AND column_name IN ('saas_plan_id', 'enabled_features_override')
            """)
            existing = {row[0] for row in cursor.fetchall()}
            if 'saas_plan_id' in existing and 'enabled_features_override' in existing:
                self.stdout.write(self.style.SUCCESS("Columns already exist. Skipping."))
                return

            # Create Feature table if not exists
            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS customers_feature (
                        id BIGSERIAL PRIMARY KEY,
                        name VARCHAR(100) NOT NULL,
                        code VARCHAR(50) NOT NULL UNIQUE,
                        description TEXT NOT NULL DEFAULT ''
                    )
                """)
                self.stdout.write("Created customers_feature table (if needed).")
            except ProgrammingError as e:
                self.stdout.write(f"Feature table: {e}")

            # Create Plan table if not exists
            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS customers_plan (
                        id BIGSERIAL PRIMARY KEY,
                        name VARCHAR(100) NOT NULL,
                        price_per_student NUMERIC(10, 2) NOT NULL DEFAULT 0,
                        description TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                    )
                """)
                self.stdout.write("Created customers_plan table (if needed).")
            except ProgrammingError as e:
                self.stdout.write(f"Plan table: {e}")

            # Create M2M table if not exists
            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS customers_plan_features (
                        id BIGSERIAL PRIMARY KEY,
                        plan_id BIGINT NOT NULL REFERENCES customers_plan(id) ON DELETE CASCADE,
                        feature_id BIGINT NOT NULL REFERENCES customers_feature(id) ON DELETE CASCADE,
                        UNIQUE(plan_id, feature_id)
                    )
                """)
                self.stdout.write("Created customers_plan_features table (if needed).")
            except ProgrammingError as e:
                self.stdout.write(f"Plan features table: {e}")

            # Add saas_plan_id column
            if 'saas_plan_id' not in existing:
                try:
                    cursor.execute("""
                        ALTER TABLE customers_school
                        ADD COLUMN saas_plan_id BIGINT NULL REFERENCES customers_plan(id) ON DELETE SET NULL
                    """)
                    self.stdout.write(self.style.SUCCESS("Added saas_plan_id column."))
                except ProgrammingError as e:
                    self.stdout.write(self.style.ERROR(f"saas_plan_id: {e}"))

            # Add enabled_features_override column
            if 'enabled_features_override' not in existing:
                try:
                    cursor.execute("""
                        ALTER TABLE customers_school
                        ADD COLUMN enabled_features_override JSONB NULL
                    """)
                    self.stdout.write(self.style.SUCCESS("Added enabled_features_override column."))
                except ProgrammingError as e:
                    self.stdout.write(self.style.ERROR(f"enabled_features_override: {e}"))

        # Record migration as applied so Django doesn't try to run it again
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO django_migrations (app, name, applied)
                    SELECT 'customers', '0006_saas_plan_and_feature', NOW()
                    WHERE NOT EXISTS (
                        SELECT 1 FROM django_migrations
                        WHERE app = 'customers' AND name = '0006_saas_plan_and_feature'
                    )
                """)
        except Exception:
            pass  # Table might not exist

        self.stdout.write(self.style.SUCCESS("Done. Run: python manage.py seed_saas_plans"))
