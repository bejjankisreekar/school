"""
One-off fix: Add is_first_login column to accounts_user when migrate_schemas
is blocked. Run: python manage.py add_is_first_login_column
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Add is_first_login column to accounts_user if missing"

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'accounts_user';
                """
            )
            existing = {row[0] for row in cursor.fetchall()}

        if "is_first_login" in existing:
            self.stdout.write(self.style.SUCCESS("Column is_first_login already exists."))
            return

        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE accounts_user ADD COLUMN is_first_login BOOLEAN NOT NULL DEFAULT FALSE;"
            )
        self.stdout.write(self.style.SUCCESS("Added column is_first_login."))

        # Record migration so future migrate won't try to re-add
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM django_migrations WHERE app = 'accounts' AND name = '0006_add_is_first_login';"
            )
            if cursor.fetchone():
                self.stdout.write("Migration accounts.0006_add_is_first_login already recorded.")
            else:
                cursor.execute(
                    "INSERT INTO django_migrations (app, name, applied) VALUES ('accounts', '0006_add_is_first_login', NOW());"
                )
                self.stdout.write(self.style.SUCCESS("Recorded migration accounts.0006_add_is_first_login."))

        self.stdout.write(self.style.SUCCESS("Done. accounts_user now has is_first_login column."))
