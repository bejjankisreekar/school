"""
Add School.platform_control_meta on public.customers_school when migration 0017 was not applied.

Run: python manage.py ensure_school_platform_control_meta_column
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


class Command(BaseCommand):
    help = "Adds customers_school.platform_control_meta on public schema if missing (PostgreSQL)."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stderr.write(self.style.ERROR("PostgreSQL only."))
            return

        sql = """
            ALTER TABLE public.customers_school
            ADD COLUMN IF NOT EXISTS "platform_control_meta" jsonb NOT NULL DEFAULT '{}'::jsonb;
        """
        with connection.cursor() as cursor:
            cursor.execute(sql)

        recorder = MigrationRecorder(connection)
        key = ("customers", "0017_school_platform_control_meta")
        applied = recorder.applied_migrations()
        if key not in applied:
            try:
                recorder.record_applied(*key)
                self.stdout.write(
                    self.style.SUCCESS(
                        "Column OK. Recorded customers 0017_school_platform_control_meta."
                    )
                )
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(
                        f"Column added but could not record migration row ({exc}). "
                        "If migrate complains later: python manage.py migrate customers 0017 --fake"
                    )
                )
        else:
            self.stdout.write(
                self.style.SUCCESS("Column OK. Migration 0017 already recorded.")
            )
