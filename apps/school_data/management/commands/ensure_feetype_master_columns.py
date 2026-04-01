"""
Add school_data_feetype.description and is_active when migrate_schemas cannot run.

Run: python manage.py ensure_feetype_master_columns
Optional: python manage.py ensure_feetype_master_columns -s <schema_name>
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


class Command(BaseCommand):
    help = "Add FeeType description and is_active columns on tenant schemas (bypasses broken migration history)."

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

        table = "school_data_feetype"
        idx_name = "school_data_feetype_is_active_billing_idx"

        for tenant in tenants:
            name = tenant.schema_name
            self.stdout.write(f"Schema: {name}")
            connection.set_schema(name, include_public=False)
            try:
                with connection.cursor() as cursor:
                    if not _column_exists(cursor, name, table, "description"):
                        cursor.execute(
                            f'ALTER TABLE "{table}" ADD COLUMN description TEXT NOT NULL DEFAULT \'\';'
                        )
                        self.stdout.write(self.style.SUCCESS("  Added column description."))
                    else:
                        self.stdout.write("  Column description already exists.")

                    if not _column_exists(cursor, name, table, "is_active"):
                        cursor.execute(
                            f'ALTER TABLE "{table}" ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true;'
                        )
                        self.stdout.write(self.style.SUCCESS("  Added column is_active."))
                    else:
                        self.stdout.write("  Column is_active already exists.")

                    cursor.execute(
                        f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON "{table}" ("is_active");'
                    )
                    self.stdout.write(self.style.SUCCESS(f"  Ensured index on is_active ({idx_name})."))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
