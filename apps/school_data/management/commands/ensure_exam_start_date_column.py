"""
Restore school_data_exam.start_date when migration 0028 dropped it but the model still
maps Exam.date -> db_column start_date (ProgrammingError on dashboard).

Run: python manage.py ensure_exam_start_date_column
Optional: python manage.py ensure_exam_start_date_column -s <schema_name>

Idempotent. Safe to run before/after migrate_schemas (migration 0038 is a no-op if column exists).
"""
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connections
from django_tenants.utils import get_tenant_database_alias, get_public_schema_name


def _restore_exam_start_date(cursor, schema_name: str) -> str:
    cursor.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = 'school_data_exam'
        """,
        [schema_name],
    )
    cols = {row[0] for row in cursor.fetchall()}
    if not cols:
        return "skip_no_table"
    if "start_date" in cols:
        return "already_ok"
    cursor.execute("ALTER TABLE school_data_exam ADD COLUMN start_date date NULL")
    if "end_date" in cols:
        cursor.execute(
            """
            UPDATE school_data_exam
            SET start_date = end_date
            WHERE start_date IS NULL AND end_date IS NOT NULL
            """
        )
    cursor.execute(
        "UPDATE school_data_exam SET start_date = CURRENT_DATE WHERE start_date IS NULL"
    )
    cursor.execute(
        "ALTER TABLE school_data_exam ALTER COLUMN start_date SET NOT NULL"
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS school_data_exam_start_date_idx
        ON school_data_exam (start_date)
        """
    )
    return "added"


class Command(BaseCommand):
    help = "Add missing start_date column on school_data_exam for tenant schemas."

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
        schema_filter = options.get("schema_name")
        School = apps.get_model("customers", "School")
        school_table = connection.ops.quote_name(School._meta.db_table)
        connection.set_schema_to_public()

        if schema_filter:
            if schema_filter == public:
                self.stdout.write(
                    self.style.WARNING("school_data_exam is tenant-only; skipping public schema.")
                )
                return
            tenant_schemas = [schema_filter]
        else:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT schema_name FROM {school_table} WHERE schema_name <> %s ORDER BY schema_name",
                    [public],
                )
                tenant_schemas = [row[0] for row in cursor.fetchall()]

        if not tenant_schemas:
            self.stdout.write(self.style.WARNING("No tenant schemas found."))
            return

        for name in tenant_schemas:
            self.stdout.write(f"Schema: {name}")
            connection.set_schema(name, include_public=False)
            try:
                with connection.cursor() as cursor:
                    result = _restore_exam_start_date(cursor, name)
                    if result == "skip_no_table":
                        self.stdout.write(
                            self.style.WARNING("  No school_data_exam table; run migrate_schemas first.")
                        )
                    elif result == "already_ok":
                        self.stdout.write(
                            self.style.SUCCESS("  Column start_date already present.")
                        )
                    else:
                        self.stdout.write(
                            self.style.SUCCESS("  Added and backfilled start_date.")
                        )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
