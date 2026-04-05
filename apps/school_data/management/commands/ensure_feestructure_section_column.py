"""
Add FeeStructure.section_id + partial unique indexes when migrate_schemas is stuck.

Run: python manage.py ensure_feestructure_section_column
Optional: python manage.py ensure_feestructure_section_column -s <schema_name>
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
    help = "Add FeeStructure.section_id and partial unique indexes on tenant schemas (fixes missing column errors)."

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
                    if not _column_exists(cursor, name, table, "section_id"):
                        cursor.execute(
                            f'ALTER TABLE "{table}" ADD COLUMN section_id bigint NULL;'
                        )
                        self.stdout.write(self.style.SUCCESS("  Added column section_id."))
                    else:
                        self.stdout.write("  Column section_id already exists.")

                    cursor.execute(
                        """
                        DO $$
                        BEGIN
                            ALTER TABLE school_data_feestructure
                                ADD CONSTRAINT school_data_feestructure_section_id_fkey
                                FOREIGN KEY (section_id) REFERENCES school_data_section(id)
                                DEFERRABLE INITIALLY DEFERRED;
                        EXCEPTION
                            WHEN duplicate_object THEN NULL;
                        END $$;
                        """
                    )
                    self.stdout.write("  Ensured FK section_id -> school_data_section.")

                    cursor.execute(
                        """
                        DO $$
                        DECLARE r record;
                        BEGIN
                            FOR r IN (
                                SELECT c.conname
                                FROM pg_constraint c
                                JOIN pg_class t ON c.conrelid = t.oid
                                WHERE t.relname = 'school_data_feestructure'
                                  AND c.contype = 'u'
                                  AND pg_get_constraintdef(c.oid) LIKE '%fee_type_id%'
                                  AND pg_get_constraintdef(c.oid) LIKE '%classroom_id%'
                                  AND pg_get_constraintdef(c.oid) LIKE '%academic_year_id%'
                                  AND pg_get_constraintdef(c.oid) NOT LIKE '%section_id%'
                            ) LOOP
                                EXECUTE format(
                                    'ALTER TABLE school_data_feestructure DROP CONSTRAINT IF EXISTS %I',
                                    r.conname
                                );
                            END LOOP;
                        END $$;
                        """
                    )
                    self.stdout.write("  Dropped legacy fee_structure unique constraint(s) if any.")

                    cursor.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS school_data_feestructure_unique_class_wide
                            ON school_data_feestructure (fee_type_id, classroom_id, academic_year_id)
                            WHERE section_id IS NULL;
                        """
                    )
                    cursor.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS school_data_feestructure_unique_section
                            ON school_data_feestructure (fee_type_id, classroom_id, academic_year_id, section_id)
                            WHERE section_id IS NOT NULL;
                        """
                    )
                    self.stdout.write(
                        self.style.SUCCESS("  Ensured partial unique indexes (class-wide + section-scoped).")
                    )

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
