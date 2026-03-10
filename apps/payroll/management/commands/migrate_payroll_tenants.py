"""
Apply only the payroll app migrations to tenant schemas.
Use when migrate_schemas fails on other apps but you need payroll tables.

Uses Django's migrate command directly (not migrate_schemas) so -s applies
to a single schema. django-tenants overrides "migrate" to run migrate_schemas,
which ignores our schema context and runs on all tenants.
"""
from django.core.management import BaseCommand
from django.core.management.commands.migrate import Command as MigrateCommand
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder
from django_tenants.utils import get_tenant_database_alias, get_tenant_model, get_public_schema_name


class Command(BaseCommand):
    help = "Apply payroll app migrations to all tenant schemas (or -s SCHEMA)."

    def add_arguments(self, parser):
        parser.add_argument(
            "-s", "--schema",
            dest="schema_name",
            help="Apply only to this tenant schema name.",
        )

    def handle(self, *args, **options):
        schema_name = options.get("schema_name")
        db_alias = get_tenant_database_alias()
        connection = connections[db_alias]
        public = get_public_schema_name()
        Tenant = get_tenant_model()

        if schema_name:
            if schema_name == public:
                self.stdout.write(self.style.WARNING("Skipping public schema (payroll is tenant-only)."))
                return
            tenants = [(schema_name,)]
        else:
            tenants = list(
                Tenant.objects.exclude(schema_name=public).values_list("schema_name", flat=True)
            )
            tenants = [(t,) for t in tenants]
            if not tenants:
                self.stdout.write(self.style.WARNING("No tenant schemas found."))
                return

        for (name,) in tenants:
            self.stdout.write(f"Applying payroll migrations to schema: {name}")
            try:
                connection.set_schema(name, include_public=False)
                recorder = MigrationRecorder(connection)
                recorder.ensure_schema()
                connection.set_schema(name)
                # Use Django's migrate command directly; call_command("migrate")
                # runs migrate_schemas (tenant override) and ignores our schema.
                MigrateCommand().run_from_argv([
                    "manage.py", "migrate", "payroll",
                    "--database", db_alias,
                    f"--verbosity={options.get('verbosity', 1)}",
                ])
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
