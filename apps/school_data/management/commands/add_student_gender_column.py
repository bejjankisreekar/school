"""
Add school_data_student.gender on tenant schemas when migrate_schemas is blocked.

Run: python manage.py add_student_gender_column
Optional: python manage.py add_student_gender_column -s <schema_name>
"""
from django.core.management.base import BaseCommand
from django.db import connections
from django_tenants.utils import get_tenant_database_alias, get_tenant_model, get_public_schema_name


MIGRATION_NAME = "0024_student_gender"


class Command(BaseCommand):
    help = "Add gender column to school_data_student on tenant schemas and record migration 0024."

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
                self.stdout.write(self.style.WARNING("school_data is tenant-only; skipping public schema."))
                return
            tenants = list(Tenant.objects.filter(schema_name=schema_filter))
            if not tenants:
                self.stdout.write(self.style.ERROR(f"No tenant with schema_name={schema_filter!r}."))
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
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = %s
                          AND table_name = 'school_data_student'
                          AND column_name = 'gender';
                        """,
                        [name],
                    )
                    if cursor.fetchone():
                        self.stdout.write(self.style.SUCCESS(f"  Column gender already exists."))
                    else:
                        cursor.execute(
                            """
                            ALTER TABLE school_data_student
                            ADD COLUMN gender VARCHAR(1) NOT NULL DEFAULT '';
                            """
                        )
                        self.stdout.write(self.style.SUCCESS("  Added column gender."))

                    cursor.execute(
                        """
                        CREATE INDEX IF NOT EXISTS school_data_student_gender_idx
                        ON school_data_student (gender);
                        """
                    )

                    cursor.execute(
                        """
                        SELECT 1 FROM django_migrations
                        WHERE app = 'school_data' AND name = %s;
                        """,
                        [MIGRATION_NAME],
                    )
                    if cursor.fetchone():
                        self.stdout.write("  Migration school_data.0024 already recorded.")
                    else:
                        cursor.execute(
                            """
                            INSERT INTO django_migrations (app, name, applied)
                            VALUES ('school_data', %s, NOW());
                            """,
                            [MIGRATION_NAME],
                        )
                        self.stdout.write(
                            self.style.SUCCESS(f"  Recorded school_data.{MIGRATION_NAME}.")
                        )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
