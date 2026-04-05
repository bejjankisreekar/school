"""
Add School.payslip_format when migrate_schemas is blocked by migration history.

Run: python manage.py ensure_school_payslip_format_column
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


class Command(BaseCommand):
    help = "Adds customers_school.payslip_format on public schema if missing (PostgreSQL)."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stderr.write(self.style.ERROR("PostgreSQL only."))
            return

        sql = '''
            ALTER TABLE public.customers_school
            ADD COLUMN IF NOT EXISTS "payslip_format" varchar(20) NOT NULL DEFAULT 'corporate';
        '''
        with connection.cursor() as cursor:
            cursor.execute(sql)

        recorder = MigrationRecorder(connection)
        key = ("customers", "0015_school_payslip_format")
        applied = recorder.applied_migrations()
        if key not in applied:
            try:
                recorder.record_applied(*key)
                self.stdout.write(self.style.SUCCESS("Column OK. Recorded customers 0015_school_payslip_format."))
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(
                        f"Column added but could not record migration row ({exc}). "
                        "If migrate complains later: python manage.py migrate customers 0015 --fake"
                    )
                )
        else:
            self.stdout.write(self.style.SUCCESS("Column OK. Migration 0015 already recorded."))
