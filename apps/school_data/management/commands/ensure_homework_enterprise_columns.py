"""
Add missing Homework columns (assigned_date, academic_year_id, etc.) on tenant schemas.

Run: python manage.py ensure_homework_enterprise_columns
Optional: python manage.py ensure_homework_enterprise_columns -s <schema_name>
"""
from django.core.management.base import BaseCommand
from django.db import connections
from django_tenants.utils import get_tenant_database_alias, get_tenant_model, get_public_schema_name

from apps.school_data.homework_schema_repair import ensure_homework_enterprise_columns


class Command(BaseCommand):
    help = "Add missing enterprise columns on school_data_homework for tenant schemas."

    def add_arguments(self, parser):
        parser.add_argument(
            "-s",
            "--schema",
            dest="schema_name",
            help="Only this tenant schema name (default: all tenants).",
        )

    def handle(self, *args, **options):
        db_alias = get_tenant_database_alias()
        connection = connections[db_alias]
        public = get_public_schema_name()
        Tenant = get_tenant_model()
        schema_filter = options.get("schema_name")

        if schema_filter:
            if schema_filter == public:
                self.stdout.write(
                    self.style.WARNING("school_data_homework is tenant-only; skipping public schema.")
                )
                return
            tenants = list(Tenant.objects.filter(schema_name=schema_filter))
            if not tenants:
                self.stdout.write(
                    self.style.ERROR(f"No tenant with schema_name={schema_filter!r}.")
                )
                return
        else:
            tenants = list(Tenant.objects.exclude(schema_name=public))

        if not tenants:
            self.stdout.write(self.style.WARNING("No tenant schemas found."))
            return

        for tenant in tenants:
            name = tenant.schema_name
            self.stdout.write(f"Schema: {name}")
            connection.set_schema(name, include_public=False)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = %s AND table_name = 'school_data_homework'
                        """,
                        [name],
                    )
                    if not cursor.fetchone():
                        self.stdout.write(
                            self.style.WARNING(
                                "  No school_data_homework table; run migrate_schemas for this tenant first."
                            )
                        )
                        continue
                ensure_homework_enterprise_columns(connection)
                self.stdout.write(
                    self.style.SUCCESS("  Homework columns ensured (and assigned_date backfilled where null).")
                )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
