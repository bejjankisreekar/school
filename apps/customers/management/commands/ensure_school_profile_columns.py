"""Add School profile columns if missing (when migrate cannot run on public schema)."""

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.db.utils import ProgrammingError

_ALTER_STATEMENTS_PG = [
    'ALTER TABLE "customers_school" ADD COLUMN IF NOT EXISTS "date_of_establishment" date NULL;',
    'ALTER TABLE "customers_school" ADD COLUMN IF NOT EXISTS "website" varchar(500) NOT NULL DEFAULT \'\';',
    'ALTER TABLE "customers_school" ADD COLUMN IF NOT EXISTS "registration_number" varchar(120) NOT NULL DEFAULT \'\';',
    'ALTER TABLE "customers_school" ADD COLUMN IF NOT EXISTS "board_affiliation" varchar(120) NOT NULL DEFAULT \'\';',
]


class Command(BaseCommand):
    help = "Ensures customers_school has institution profile columns (PostgreSQL)."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stderr.write(self.style.ERROR("PostgreSQL only."))
            return

        with connection.cursor() as cursor:
            for sql in _ALTER_STATEMENTS_PG:
                cursor.execute(sql)

        recorder = MigrationRecorder(connection)
        key = ("customers", "0014_school_public_profile_fields")
        if key not in recorder.applied_migrations():
            try:
                recorder.record_applied(*key)
            except Exception:
                self.stdout.write(
                    self.style.WARNING(
                        "Columns added; could not record migration row (duplicate or DB constraint). "
                        "You may run: python manage.py migrate customers 0014 --fake"
                    )
                )
                return

        self.stdout.write(self.style.SUCCESS("School profile columns are present. Migration 0014 recorded as applied."))
