"""
Add school_data_exam.room, details, topics when the model has them but DB does not
(ProgrammingError on dashboard).

Run: python manage.py ensure_exam_room_details_topics_columns
Optional: python manage.py ensure_exam_room_details_topics_columns -s <schema_name>

Idempotent. Complements migration 0052_exam_room_details_topics when migrate_schemas is blocked.
"""

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connections
from django_tenants.utils import get_tenant_database_alias, get_public_schema_name


def _ensure_columns(cursor, schema_name: str) -> list[str]:
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
        return ["skip_no_table"]
    added = []
    if "room" not in cols:
        cursor.execute(
            "ALTER TABLE school_data_exam ADD COLUMN room varchar(120) NOT NULL DEFAULT ''"
        )
        added.append("room")
    if "details" not in cols:
        cursor.execute(
            "ALTER TABLE school_data_exam ADD COLUMN details text NOT NULL DEFAULT ''"
        )
        added.append("details")
    if "topics" not in cols:
        cursor.execute(
            "ALTER TABLE school_data_exam ADD COLUMN topics text NOT NULL DEFAULT ''"
        )
        added.append("topics")
    return added if added else ["already_ok"]


class Command(BaseCommand):
    help = "Add missing room/details/topics columns on school_data_exam for tenant schemas."

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
                    result = _ensure_columns(cursor, name)
                    if result == ["skip_no_table"]:
                        self.stdout.write(
                            self.style.WARNING("  No school_data_exam table; run migrate_schemas first.")
                        )
                    elif result == ["already_ok"]:
                        self.stdout.write(
                            self.style.SUCCESS("  Columns room/details/topics already present.")
                        )
                    else:
                        self.stdout.write(
                            self.style.SUCCESS(f"  Added: {', '.join(result)}")
                        )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            finally:
                connection.set_schema_to_public()

        self.stdout.write(self.style.SUCCESS("Done."))
