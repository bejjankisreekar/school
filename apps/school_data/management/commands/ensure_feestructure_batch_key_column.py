"""
Add FeeStructure.batch_key when migration 0065 is not applied (e.g. migrate_schemas blocked).

Run: python manage.py ensure_feestructure_batch_key_column
Optional: python manage.py ensure_feestructure_batch_key_column -s <schema_name>
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
    help = "Add FeeStructure.batch_key (UUID) on tenant schemas when the column is missing."

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

        table = "school_data_feestructure"

        for tenant in tenants:
            name = tenant.schema_name
            self.stdout.write(f"Schema: {name}")
            connection.set_schema(name, include_public=False)
            try:
                with connection.cursor() as cursor:
                    if not _column_exists(cursor, name, table, "batch_key"):
                        cursor.execute(
                            f'ALTER TABLE "{table}" ADD COLUMN batch_key uuid NULL;'
                        )
                        self.stdout.write(self.style.SUCCESS("  Added column batch_key."))
                    else:
                        self.stdout.write("  Column batch_key already exists.")

                    cursor.execute(
                        """
                        CREATE INDEX IF NOT EXISTS school_data_feestructure_batch_key_idx
                        ON school_data_feestructure (batch_key);
                        """
                    )
                    self.stdout.write("  Ensured index on batch_key.")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
