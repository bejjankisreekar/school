"""
Add Exam.subject_id and Exam.total_marks to tenant schema(s) when migrate fails
(e.g. InconsistentMigrationHistory). Also records school_data.0014 if missing.

Run: python manage.py apply_exam_subject_columns
     python manage.py apply_exam_subject_columns -s your_tenant_schema
"""
from django.core.management.base import BaseCommand
from django.db import connection
from django_tenants.utils import get_tenant_model, get_public_schema_name


class Command(BaseCommand):
    help = "Add subject_id and total_marks to school_data_exam in tenant schema(s)"

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

                    if "subject_id" not in cols:
                        cursor.execute(
                            """
                            ALTER TABLE school_data_exam
                            ADD COLUMN subject_id BIGINT NULL
                            REFERENCES school_data_subject(id) ON DELETE CASCADE
                            """
                        )
                        self.stdout.write(self.style.SUCCESS("  Added subject_id"))
                    else:
                        self.stdout.write("  subject_id already present")

                    if "total_marks" not in cols:
                        cursor.execute(
                            """
                            ALTER TABLE school_data_exam
                            ADD COLUMN total_marks INTEGER NULL
                            """
                        )
                        cursor.execute(
                            "UPDATE school_data_exam SET total_marks = 100 WHERE total_marks IS NULL"
                        )
                        self.stdout.write(self.style.SUCCESS("  Added total_marks (default 100 for existing rows)"))
                    else:
                        self.stdout.write("  total_marks already present")

                    cursor.execute(
                        """
                        SELECT 1 FROM django_migrations
                        WHERE app = 'school_data' AND name = '0014_exam_subject_total_marks'
                        """
                    )
                    if not cursor.fetchone():
                        cursor.execute(
                            """
                            INSERT INTO django_migrations (app, name, applied)
                            VALUES ('school_data', '0014_exam_subject_total_marks', NOW())
                            """
                        )
                        self.stdout.write(self.style.SUCCESS("  Recorded migration 0014_exam_subject_total_marks"))
                    else:
                        self.stdout.write("  Migration 0014 already recorded")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
