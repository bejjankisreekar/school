from __future__ import annotations

from django.core.management.base import BaseCommand
from django_tenants.utils import schema_exists

from apps.core.db_schema_utils import missing_tables
from apps.customers.models import School


REQUIRED_TENANT_TABLES = (
    "school_data_academicyear",
    "school_data_classroom",
    "school_data_section",
    "school_data_student",
)


class Command(BaseCommand):
    help = "Validate required tenant tables exist per school schema (no migrations run)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            type=str,
            default="",
            help="Optional: validate only one tenant schema_name",
        )

    def handle(self, *args, **options):
        only_schema = (options.get("schema") or "").strip()

        qs = School.objects.all().order_by("schema_name").only("code", "name", "schema_name")
        if only_schema:
            qs = qs.filter(schema_name=only_schema)

        schools = list(qs)
        if not schools:
            self.stdout.write(self.style.WARNING("No matching School rows found."))
            return

        self.stdout.write(self.style.NOTICE(f"Checking {len(schools)} schema(s)..."))
        any_missing = False

        for s in schools:
            schema = s.schema_name
            if not schema_exists(schema):
                any_missing = True
                self.stdout.write(self.style.ERROR(f"{schema:20} | MISSING SCHEMA | {s.code} | {s.name}"))
                continue

            miss = missing_tables(schema, REQUIRED_TENANT_TABLES)
            if miss:
                any_missing = True
                self.stdout.write(
                    self.style.ERROR(f"{schema:20} | missing {', '.join(miss)}")
                    + f" | {s.code} | {s.name}"
                )
            else:
                self.stdout.write(self.style.SUCCESS(f"{schema:20} | OK") + f" | {s.code} | {s.name}")

        if any_missing:
            self.stdout.write("")
            self.stdout.write("Fix strategy (run OUTSIDE requests):")
            self.stdout.write(self.style.SUCCESS("  python manage.py migrate_schemas"))
            self.stdout.write("Or fix one tenant:")
            self.stdout.write(self.style.SUCCESS("  python manage.py migrate_schemas -s <schema_name>"))

