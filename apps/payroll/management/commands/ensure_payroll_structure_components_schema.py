"""
Fix: ProgrammingError - column payroll_salarystructure.use_default_salary_components does not exist

Cause: Model + code expect payroll migration 0004 (per-employee component selection), but the
tenant schema was never migrated, so PostgreSQL has no column or M2M table.

This command (per tenant schema):
  1. Adds use_default_salary_components (boolean, default true) if missing
  2. Creates payroll_salarystructure_applicable_components if missing
  3. Fakes migration 0004_salarystructure_applicable_components when not recorded
     (requires payroll 0001-0003 already recorded on that schema)

Usage:
    python manage.py ensure_payroll_structure_components_schema
    python manage.py ensure_payroll_structure_components_schema -s your_schema_name
"""
from django.core.management import BaseCommand
from django.core.management.commands.migrate import Command as MigrateCommand
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder
from django_tenants.utils import get_tenant_database_alias, get_tenant_model, get_public_schema_name

MIGRATION_0004 = "0004_salarystructure_applicable_components"
MIGRATION_0005 = "0005_salarystructure_component_overrides"
STRUCT_TABLE = "payroll_salarystructure"
M2M_TABLE = "payroll_salarystructure_applicable_components"


class Command(BaseCommand):
    help = (
        "Apply payroll 0004/0005 DDL on tenants (structure flag, M2M table, per-employee override columns); "
        "fake migrations when needed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "-s",
            "--schema",
            dest="schema_name",
            help="Only this tenant schema name.",
        )

    def _column_exists(self, cursor, table: str, column: str) -> bool:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
              AND column_name = %s
            """,
            [table, column],
        )
        return cursor.fetchone() is not None

    def _table_exists(self, cursor, table: str) -> bool:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = %s
            """,
            [table],
        )
        return cursor.fetchone() is not None

    def handle(self, *args, **options):
        schema_name = options.get("schema_name")
        db_alias = get_tenant_database_alias()
        connection = connections[db_alias]
        public = get_public_schema_name()
        Tenant = get_tenant_model()

        if schema_name:
            if schema_name == public:
                self.stdout.write(self.style.WARNING("Payroll lives on tenant schemas only; skipping public."))
                return
            tenants = [schema_name]
        else:
            tenants = list(
                Tenant.objects.exclude(schema_name=public).values_list("schema_name", flat=True)
            )
            if not tenants:
                self.stdout.write(self.style.WARNING("No tenant schemas found."))
                return

        for name in tenants:
            self.stdout.write(f"Schema: {name}")
            try:
                connection.set_schema(name, include_public=False)
                connection.set_schema(name)
                recorder = MigrationRecorder(connection)
                recorder.ensure_schema()
                mig4 = ("payroll", MIGRATION_0004) in recorder.applied_migrations()
                mig5 = ("payroll", MIGRATION_0005) in recorder.applied_migrations()

                with connection.cursor() as cursor:
                    if not self._table_exists(cursor, STRUCT_TABLE):
                        self.stdout.write(
                            self.style.ERROR(
                                f"  Table {STRUCT_TABLE} missing - apply payroll 0001 on this schema first."
                            )
                        )
                        continue

                    if not self._column_exists(cursor, STRUCT_TABLE, "use_default_salary_components"):
                        cursor.execute(
                            "ALTER TABLE payroll_salarystructure "
                            "ADD COLUMN use_default_salary_components boolean NOT NULL DEFAULT true"
                        )
                        self.stdout.write(self.style.SUCCESS('  Added column "use_default_salary_components".'))
                    else:
                        self.stdout.write('  Column "use_default_salary_components" already present.')

                    if not self._table_exists(cursor, M2M_TABLE):
                        cursor.execute(
                            f"""
                            CREATE TABLE {M2M_TABLE} (
                                id BIGSERIAL NOT NULL PRIMARY KEY,
                                salarystructure_id BIGINT NOT NULL
                                    REFERENCES payroll_salarystructure (id) ON DELETE CASCADE,
                                salarycomponent_id BIGINT NOT NULL
                                    REFERENCES payroll_salarycomponent (id) ON DELETE CASCADE,
                                UNIQUE (salarystructure_id, salarycomponent_id)
                            )
                            """
                        )
                        self.stdout.write(self.style.SUCCESS(f'  Created table "{M2M_TABLE}".'))
                    else:
                        self.stdout.write(f'  Table "{M2M_TABLE}" already present.')

                    if self._table_exists(cursor, M2M_TABLE):
                        if not self._column_exists(cursor, M2M_TABLE, "use_component_default"):
                            cursor.execute(
                                "ALTER TABLE payroll_salarystructure_applicable_components "
                                "ADD COLUMN use_component_default boolean NOT NULL DEFAULT true"
                            )
                            self.stdout.write(self.style.SUCCESS('  Added M2M "use_component_default".'))
                        if not self._column_exists(cursor, M2M_TABLE, "override_calculation_type"):
                            cursor.execute(
                                "ALTER TABLE payroll_salarystructure_applicable_components "
                                "ADD COLUMN override_calculation_type varchar(20) NOT NULL DEFAULT ''"
                            )
                            self.stdout.write(self.style.SUCCESS('  Added M2M "override_calculation_type".'))
                        if not self._column_exists(cursor, M2M_TABLE, "override_value"):
                            cursor.execute(
                                "ALTER TABLE payroll_salarystructure_applicable_components "
                                "ADD COLUMN override_value numeric(12,2) NULL"
                            )
                            self.stdout.write(self.style.SUCCESS('  Added M2M "override_value".'))

                if not mig4:
                    try:
                        MigrateCommand().run_from_argv(
                            [
                                "manage.py",
                                "migrate",
                                "payroll",
                                MIGRATION_0004,
                                "--fake",
                                "--database",
                                db_alias,
                                "--noinput",
                                "--verbosity=0",
                            ]
                        )
                        self.stdout.write(self.style.SUCCESS(f"  Faked migration {MIGRATION_0004}."))
                    except Exception as ex:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  Could not fake {MIGRATION_0004}: {ex}\n"
                                f"  Run: python manage.py migrate_payroll_tenants -s {name}"
                            )
                        )
                else:
                    self.stdout.write(f"  Migration {MIGRATION_0004} already recorded.")

                if not mig5:
                    try:
                        MigrateCommand().run_from_argv(
                            [
                                "manage.py",
                                "migrate",
                                "payroll",
                                MIGRATION_0005,
                                "--fake",
                                "--database",
                                db_alias,
                                "--noinput",
                                "--verbosity=0",
                            ]
                        )
                        self.stdout.write(self.style.SUCCESS(f"  Faked migration {MIGRATION_0005}."))
                    except Exception as ex:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  Could not fake {MIGRATION_0005}: {ex}\n"
                                f"  Run: python manage.py migrate_payroll_tenants -s {name}"
                            )
                        )
                else:
                    self.stdout.write(f"  Migration {MIGRATION_0005} already recorded.")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
