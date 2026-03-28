"""
List every tenant school with its PostgreSQL schema name (for migrate_schemas -s).

Run: python manage.py list_school_schemas

The value in "schema_name" is what django-tenants expects:
  python manage.py migrate_schemas -s <schema_name>

School "code" (e.g. GVS001) is not always the same as schema_name (e.g. gvs001).
"""
from django.core.management.base import BaseCommand

from django_tenants.utils import schema_exists

from apps.customers.models import School


class Command(BaseCommand):
    help = "List schools with schema_name for migrate_schemas -s"

    def handle(self, *args, **options):
        schools = School.objects.all().order_by("schema_name")
        if not schools.exists():
            self.stdout.write(self.style.WARNING("No School (tenant) rows in the public schema."))
            return

        self.stdout.write(self.style.NOTICE("School code / name / PostgreSQL schema_name / exists"))
        for s in schools:
            exists = schema_exists(s.schema_name)
            ok = self.style.SUCCESS("yes") if exists else self.style.ERROR("NO")
            self.stdout.write(
                f"  {s.code:12} | {s.name[:40]:40} | {s.schema_name:20} | schema {ok}"
            )
        self.stdout.write("")
        self.stdout.write("Migrate one tenant:")
        self.stdout.write(self.style.SUCCESS("  python manage.py migrate_schemas -s <schema_name>"))
        self.stdout.write("Migrate every tenant:")
        self.stdout.write(self.style.SUCCESS("  python manage.py migrate_schemas"))
