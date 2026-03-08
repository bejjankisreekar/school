"""
One-off fix: Add branding columns to customers_school when migrate_schemas
is blocked by other migrations. Run: python manage.py add_logo_columns
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Add logo, theme_color, header_text, custom_domain, is_single_tenant to customers_school"

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'customers_school';
                """
            )
            existing = {row[0] for row in cursor.fetchall()}

        to_add = [
            ("logo", "ALTER TABLE customers_school ADD COLUMN logo VARCHAR(100) NULL;"),
            ("theme_color", "ALTER TABLE customers_school ADD COLUMN theme_color VARCHAR(20) NOT NULL DEFAULT '#4F46E5';"),
            ("header_text", "ALTER TABLE customers_school ADD COLUMN header_text VARCHAR(200) NOT NULL DEFAULT '';"),
            ("custom_domain", "ALTER TABLE customers_school ADD COLUMN custom_domain VARCHAR(255) NOT NULL DEFAULT '';"),
            ("is_single_tenant", "ALTER TABLE customers_school ADD COLUMN is_single_tenant BOOLEAN NOT NULL DEFAULT FALSE;"),
        ]

        for col, sql in to_add:
            if col in existing:
                self.stdout.write(f"Column {col} already exists, skipping.")
            else:
                with connection.cursor() as cursor:
                    cursor.execute(sql)
                self.stdout.write(self.style.SUCCESS(f"Added column {col}."))

        # Mark migration as applied so future migrate_schemas won't try to re-add
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM django_migrations WHERE app = 'customers' AND name = '0003_add_pro_plan_features';"
            )
            if cursor.fetchone():
                self.stdout.write("Migration customers.0003 already recorded.")
            else:
                cursor.execute(
                    "INSERT INTO django_migrations (app, name, applied) VALUES ('customers', '0003_add_pro_plan_features', NOW());"
                )
                self.stdout.write(self.style.SUCCESS("Recorded migration customers.0003_add_pro_plan_features."))

        self.stdout.write(self.style.SUCCESS("Done. customers_school now has branding columns."))
