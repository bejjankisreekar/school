"""
Add AcademicYear.description and wizard_settings when migrate_schemas cannot run.

Run:
    python manage.py ensure_academic_year_wizard_columns
Optional:
    python manage.py ensure_academic_year_wizard_columns -s <schema_name>
"""
from django.core.management.base import BaseCommand
from django.db import connections
from django_tenants.utils import get_tenant_database_alias, get_tenant_model, get_public_schema_name


def _column_exists(cursor, schema: str, table: str, column: str) -> bool:
    cursor.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s AND column_name = %s;
        """,
        [schema, table, column],
    )
    return cursor.fetchone() is not None


def _table_exists(cursor, schema: str, table: str) -> bool:
    cursor.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s;
        """,
        [schema, table],
    )
    return cursor.fetchone() is not None


class Command(BaseCommand):
    help = "Add AcademicYear description and wizard_settings on tenant schemas."

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
        table = "school_data_academicyear"

        if schema_filter:
            if schema_filter == public:
                self.stdout.write(
                    self.style.WARNING("school_data is tenant-only; skipping public schema.")
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
                    if not _table_exists(cursor, name, table):
                        self.stdout.write(
                            self.style.WARNING(f"  Table {table} missing; skip schema.")
                        )
                        continue

                    if not _column_exists(cursor, name, table, "description"):
                        cursor.execute(
                            f'ALTER TABLE "{table}" ADD COLUMN description TEXT NOT NULL DEFAULT \'\';'
                        )
                        self.stdout.write(self.style.SUCCESS("  Added column description."))
                    else:
                        self.stdout.write("  Column description already exists.")

                    if not _column_exists(cursor, name, table, "wizard_settings"):
                        cursor.execute(
                            f'ALTER TABLE "{table}" ADD COLUMN wizard_settings JSONB NOT NULL DEFAULT '
                            f"'{{}}'::jsonb;"
                        )
                        self.stdout.write(self.style.SUCCESS("  Added column wizard_settings."))
                    else:
                        self.stdout.write("  Column wizard_settings already exists.")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
