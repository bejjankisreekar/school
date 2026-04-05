"""
Create school_data holiday calendar tables on tenant schemas when migration 0051 was not applied.

Run (all tenants):
    python manage.py ensure_holiday_calendar_tables

Run (one schema):
    python manage.py ensure_holiday_calendar_tables -s <schema_name>

Safe to run multiple times: skips if tables already exist. Records migration 0051 in django_migrations
when tables are created (or when tables exist but the migration row is missing).
"""
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder
from django.db.utils import DatabaseError, ProgrammingError
from django_tenants.utils import get_tenant_database_alias, get_tenant_model, get_public_schema_name

from apps.school_data.models import HolidayCalendar, HolidayEvent, WorkingSundayOverride

MIGRATION_KEY = ("school_data", "0051_holiday_calendar")


class Command(BaseCommand):
    help = "Create HolidayCalendar / HolidayEvent / WorkingSundayOverride tables on tenant schemas."

    def add_arguments(self, parser):
        parser.add_argument(
            "-s",
            "--schema",
            dest="schema_name",
            help="Only this tenant schema name (default: all tenants).",
        )

    def _table_exists(self, connection, schema_name: str, table: str) -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s
                """,
                [schema_name, table],
            )
            return cursor.fetchone() is not None

    def _migration_applied(self, connection) -> bool:
        recorder = MigrationRecorder(connection)
        if not recorder.has_table():
            return False
        return MIGRATION_KEY in recorder.applied_migrations()

    def _record_migration(self, connection):
        recorder = MigrationRecorder(connection)
        if MIGRATION_KEY not in recorder.applied_migrations():
            recorder.record_applied(MIGRATION_KEY[0], MIGRATION_KEY[1])
            self.stdout.write("    Recorded django_migrations row for 0051_holiday_calendar.")

    def handle(self, *args, **options):
        db_alias = get_tenant_database_alias()
        connection = connections[db_alias]
        public = get_public_schema_name()
        Tenant = get_tenant_model()
        schema_filter = options.get("schema_name")

        if schema_filter:
            if schema_filter == public:
                self.stdout.write(
                    self.style.WARNING("Holiday calendar is tenant-only; skipping public schema.")
                )
                return
            tenants = list(Tenant.objects.filter(schema_name=schema_filter))
            if not tenants:
                self.stdout.write(self.style.ERROR(f"No tenant with schema_name={schema_filter!r}."))
                return
        else:
            tenants = list(Tenant.objects.exclude(schema_name=public))

        if not tenants:
            self.stdout.write(self.style.WARNING("No tenant schemas found."))
            return

        for tenant in tenants:
            name = tenant.schema_name
            self.stdout.write(f"Schema: {name}")
            # include_public=True so FK references to accounts_user (public schema) resolve.
            connection.set_schema(name, include_public=True)
            try:
                has_cal = self._table_exists(connection, name, "school_data_holidaycalendar")
                mig_ok = self._migration_applied(connection)

                if has_cal and mig_ok:
                    self.stdout.write(self.style.SUCCESS("  Holiday calendar tables already present."))
                    continue

                if has_cal and not mig_ok:
                    self._record_migration(connection)
                    self.stdout.write(
                        self.style.SUCCESS("  Tables existed; migration history updated only.")
                    )
                    continue

                # Create tables
                self.stdout.write("  Creating holiday calendar tables...")
                try:
                    with connection.schema_editor() as editor:
                        editor.create_model(HolidayCalendar)
                        editor.create_model(HolidayEvent)
                        editor.create_model(WorkingSundayOverride)
                except (ProgrammingError, DatabaseError) as exc:
                    self.stdout.write(self.style.ERROR(f"  create_model failed: {exc}"))
                    connection.rollback()
                    continue

                self._record_migration(connection)
                self.stdout.write(self.style.SUCCESS("  Done."))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Finished."))
