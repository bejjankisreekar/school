"""
Add Exam.marks_teacher_edit_locked when migrate cannot run (e.g. InconsistentMigrationHistory).

Run: python manage.py apply_exam_marks_lock_column
     python manage.py apply_exam_marks_lock_column -s your_tenant_schema
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django_tenants.utils import get_tenant_model, get_public_schema_name


class Command(BaseCommand):
    help = "Add marks_teacher_edit_locked to school_data_exam in tenant schema(s)"

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

                    if "marks_teacher_edit_locked" not in cols:
                        cursor.execute(
                            """
                            ALTER TABLE school_data_exam
                            ADD COLUMN marks_teacher_edit_locked boolean NOT NULL DEFAULT false
                            """
                        )
                        self.stdout.write(self.style.SUCCESS("  Added marks_teacher_edit_locked"))
                    else:
                        self.stdout.write("  marks_teacher_edit_locked already present")

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
