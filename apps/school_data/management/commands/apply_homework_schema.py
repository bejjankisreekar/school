"""
Apply Homework model schema changes (assigned_by, classes, sections) via raw SQL.
Use when migrate fails due to InconsistentMigrationHistory.

Run: python manage.py apply_homework_schema
     python manage.py apply_homework_schema -s schema_name  # single tenant
"""
from django.core.management.base import BaseCommand
from django.db import connection
from django_tenants.utils import get_tenant_model, get_public_schema_name


class Command(BaseCommand):
    help = "Apply Homework class/section schema changes to tenant schema(s) via raw SQL"

    def add_arguments(self, parser):
        parser.add_argument("-s", "--schema", dest="schema", help="Apply only to this schema")

    def handle(self, *args, **options):
        schema_arg = options.get("schema")
        Tenant = get_tenant_model()
        public = get_public_schema_name()

        if schema_arg:
            schemas = [schema_arg]
        else:
            schemas = list(
                Tenant.objects.exclude(schema_name=public).values_list("schema_name", flat=True)
            )
            if not schemas:
                self.stdout.write(self.style.WARNING("No tenant schemas found. Add -s SCHEMA for a specific schema."))
                return

        sql_statements = [
            "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS assigned_by_id INTEGER NULL REFERENCES public.accounts_user(id) ON DELETE CASCADE",
            "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL",
            "ALTER TABLE school_data_homework ALTER COLUMN subject_id DROP NOT NULL",
            "ALTER TABLE school_data_homework ALTER COLUMN teacher_id DROP NOT NULL",
            """CREATE TABLE IF NOT EXISTS school_data_homework_classes (
                id SERIAL PRIMARY KEY,
                homework_id BIGINT NOT NULL REFERENCES school_data_homework(id) ON DELETE CASCADE,
                classroom_id BIGINT NOT NULL REFERENCES school_data_classroom(id) ON DELETE CASCADE,
                UNIQUE(homework_id, classroom_id)
            )""",
            """CREATE TABLE IF NOT EXISTS school_data_homework_sections (
                id SERIAL PRIMARY KEY,
                homework_id BIGINT NOT NULL REFERENCES school_data_homework(id) ON DELETE CASCADE,
                section_id BIGINT NOT NULL REFERENCES school_data_section(id) ON DELETE CASCADE,
                UNIQUE(homework_id, section_id)
            )""",
        ]

        for schema_name in schemas:
            self.stdout.write(f"Applying to schema: {schema_name}")
            try:
                connection.set_schema(schema_name)
                with connection.cursor() as cursor:
                    for sql in sql_statements:
                        try:
                            cursor.execute(sql)
                            self.stdout.write(f"  OK: {sql[:60]}...")
                        except Exception as e:
                            if "already exists" in str(e) or "duplicate" in str(e).lower():
                                self.stdout.write(self.style.WARNING(f"  Skip (exists): {e}"))
                            else:
                                raise
                self.stdout.write(self.style.SUCCESS(f"  Schema {schema_name}: done"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done. The homework page should now work."))
