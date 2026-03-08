"""
Create the public tenant and Domain for localhost/127.0.0.1.
Run after: python manage.py migrate_schemas --shared

Usage:
    python manage.py setup_public_tenant
"""
from django.core.management.base import BaseCommand
from django.db import connection

from apps.customers.models import School, Domain


class Command(BaseCommand):
    help = "Create public tenant and Domain for localhost/127.0.0.1 (main site)"

    def handle(self, *args, **options):
        if School.objects.filter(schema_name="public").exists():
            school = School.objects.get(schema_name="public")
            created = False
        else:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO customers_school
                    (schema_name, name, code, created_on, is_active, auto_create_schema, auto_drop_schema,
                     address, contact_email, phone)
                    VALUES ('public', 'Platform', 'PUBLIC', CURRENT_TIMESTAMP, true, false, false, '', '', '')
                    RETURNING id;
                    """,
                )
                row = cursor.fetchone()
                school_id = row[0] if row else None
            school = School.objects.get(pk=school_id)
            created = True

        if created:
            self.stdout.write(self.style.SUCCESS("Created public tenant (schema_name='public')"))
        else:
            self.stdout.write("Public tenant already exists.")

        for domain_name in ("localhost", "127.0.0.1"):
            _, created = Domain.objects.get_or_create(
                domain=domain_name,
                defaults={"tenant": school, "is_primary": domain_name == "localhost"},
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created Domain: {domain_name}"))
            else:
                self.stdout.write(f"Domain {domain_name} already exists.")

        self.stdout.write(self.style.SUCCESS("Setup complete. Main site available at http://localhost:8000"))
