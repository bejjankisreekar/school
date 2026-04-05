"""
Add payroll_salarycomponent.code and .description if missing (tenant schemas),
then record migration 0002 as applied so migrate does not try to add them again.

Use when the model includes code/description but tenant DB was never migrated:

    python manage.py ensure_salary_component_columns
    python manage.py ensure_salary_component_columns -s school_001
"""
from django.core.management import BaseCommand
from django.core.management.commands.migrate import Command as MigrateCommand
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder
from django_tenants.utils import get_tenant_database_alias, get_tenant_model, get_public_schema_name

MIGRATION_NAME = "0002_salarycomponent_code_description"
TABLE = "payroll_salarycomponent"


class Command(BaseCommand):
    help = "Add missing SalaryComponent code/description columns on tenant schemas and fake-apply 0002."

    def add_arguments(self, parser):
        parser.add_argument(
            "-s",
            "--schema",
            dest="schema_name",
            help="Only this tenant schema name.",
        )

    def _column_exists(self, cursor, column: str) -> bool:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
              AND column_name = %s
            """,
            [TABLE, column],
        )
        return cursor.fetchone() is not None

    def _table_exists(self, cursor) -> bool:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = %s
            """,
            [TABLE],
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
                self.stdout.write(self.style.WARNING("Payroll tables are not on the public schema; skipping."))
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
                mig_applied = ("payroll", MIGRATION_NAME) in recorder.applied_migrations()

                with connection.cursor() as cursor:
                    if not self._table_exists(cursor):
                        self.stdout.write(
                            self.style.ERROR(
                                f"  Table {TABLE} missing - run payroll migrations (0001) on this schema first."
                            )
                        )
                        continue
                    if not self._column_exists(cursor, "code"):
                        cursor.execute(
                            "ALTER TABLE payroll_salarycomponent ADD COLUMN code varchar(40) NOT NULL DEFAULT ''"
                        )
                        self.stdout.write(self.style.SUCCESS('  Added column "code".'))
                    elif not mig_applied:
                        self.stdout.write('  Column "code" already present.')
                    if not self._column_exists(cursor, "description"):
                        cursor.execute(
                            "ALTER TABLE payroll_salarycomponent ADD COLUMN description text NOT NULL DEFAULT ''"
                        )
                        self.stdout.write(self.style.SUCCESS('  Added column "description".'))
                    elif not mig_applied:
                        self.stdout.write('  Column "description" already present.')

                if not mig_applied:
                    MigrateCommand().run_from_argv(
                        [
                            "manage.py",
                            "migrate",
                            "payroll",
                            MIGRATION_NAME,
                            "--fake",
                            "--database",
                            db_alias,
                            "--noinput",
                            "--verbosity=0",
                        ]
                    )
                    self.stdout.write(self.style.SUCCESS(f"  Faked migration {MIGRATION_NAME}."))
                else:
                    self.stdout.write(f"  Migration {MIGRATION_NAME} already recorded.")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
