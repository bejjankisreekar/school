"""
Ensure tenant PostgreSQL schemas exist and run migrations.
Creates schema if missing, then migrate_schemas.
Run: python manage.py ensure_tenant_schemas
"""
from django.core.management.base import BaseCommand

from apps.customers.models import School


class Command(BaseCommand):
    help = "Create missing tenant schemas and run migrations"

    def handle(self, *args, **options):
        public = "public"
        for school in School.objects.exclude(schema_name=public):
            try:
                school.create_schema(check_if_exists=True)
                self.stdout.write(self.style.SUCCESS(f"Schema ready: {school.schema_name}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Schema {school.schema_name}: {e}"))

        from django.core.management import call_command
        call_command("migrate_schemas")
