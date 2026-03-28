"""
Add Exam.start_time and Exam.end_time to tenant schema(s) when migrate fails.
Also records school_data.0017_exam_start_end_time if missing.

Run: python manage.py apply_exam_time_columns
     python manage.py apply_exam_time_columns -s your_tenant_schema
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django_tenants.utils import get_tenant_model, get_public_schema_name


class Command(BaseCommand):
    help = "Add start_time and end_time to school_data_exam in tenant schema(s)"

    def add_arguments(self, parser):
        parser.add_argument("-s", "--schema", dest="schema", help="Apply only to this schema name")

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
                self.stdout.write(
                    self.style.WARNING("No tenant schemas found. Use -s SCHEMA for one school.")
                )
                return

        for schema_name in schemas:
            self.stdout.write(f"Schema: {schema_name}")
            try:
                connection.set_schema(schema_name)
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = %s AND table_name = 'school_data_exam'
                        """,
                        [schema_name],
                    )
                    cols = {row[0] for row in cursor.fetchall()}

                    if "start_time" not in cols:
                        cursor.execute(
                            "ALTER TABLE school_data_exam ADD COLUMN start_time TIME NULL"
                        )
                        self.stdout.write(self.style.SUCCESS("  Added start_time"))
                    else:
                        self.stdout.write("  start_time already present")

                    if "end_time" not in cols:
                        cursor.execute(
                            "ALTER TABLE school_data_exam ADD COLUMN end_time TIME NULL"
                        )
                        self.stdout.write(self.style.SUCCESS("  Added end_time"))
                    else:
                        self.stdout.write("  end_time already present")

                    cursor.execute(
                        """
                        SELECT 1 FROM django_migrations
                        WHERE app = 'school_data' AND name = '0017_exam_start_end_time'
                        """
                    )
                    if not cursor.fetchone():
                        cursor.execute(
                            """
                            INSERT INTO django_migrations (app, name, applied)
                            VALUES ('school_data', '0017_exam_start_end_time', NOW())
                            """
                        )
                        self.stdout.write(
                            self.style.SUCCESS("  Recorded migration 0017_exam_start_end_time")
                        )
                    else:
                        self.stdout.write("  Migration 0017 already recorded")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
