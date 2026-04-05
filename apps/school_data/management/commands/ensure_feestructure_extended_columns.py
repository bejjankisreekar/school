"""
Add FeeStructure billing columns (line_name, first_due_date, etc.) when migration 0048
did not run on a tenant schema.

Run: python manage.py ensure_feestructure_extended_columns
Optional: python manage.py ensure_feestructure_extended_columns -s <schema_name>
"""
from django.core.management.base import BaseCommand
from django.db import connections
from django_tenants.utils import get_tenant_database_alias, get_tenant_model, get_public_schema_name


class Command(BaseCommand):
    help = "Add extended FeeStructure columns on tenant schemas (fixes missing line_name errors)."

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

        sql = """
            ALTER TABLE school_data_feestructure
                ADD COLUMN IF NOT EXISTS line_name varchar(200) NOT NULL DEFAULT '';
            ALTER TABLE school_data_feestructure
                ADD COLUMN IF NOT EXISTS first_due_date date NULL;
            ALTER TABLE school_data_feestructure
                ADD COLUMN IF NOT EXISTS installments_enabled boolean NOT NULL DEFAULT false;
            ALTER TABLE school_data_feestructure
                ADD COLUMN IF NOT EXISTS late_fine_rule varchar(120) NOT NULL DEFAULT '';
            ALTER TABLE school_data_feestructure
                ADD COLUMN IF NOT EXISTS discount_allowed boolean NOT NULL DEFAULT true;
        """

        for tenant in tenants:
            name = tenant.schema_name
            self.stdout.write(f"Schema: {name}")
            connection.set_schema(name, include_public=False)
            try:
                with connection.cursor() as c:
                    c.execute(sql)
                self.stdout.write(self.style.SUCCESS("  Extended FeeStructure columns ensured."))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
