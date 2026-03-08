"""
Create tenant schools (GVS001, BRS001) and reassign demo users.
Run: python manage.py setup_demo_tenants

Useful when gvs_admin1 etc. have school=None and pages don't load.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from apps.customers.models import School, Domain

User = get_user_model()

SCHOOLS_SPEC = [
    {"code": "GVS001", "name": "Green Valley School", "schema_name": "gvs001", "prefix": "gvs"},
    {"code": "BRS001", "name": "Blue Ridge School", "schema_name": "brs001", "prefix": "brs"},
]


class Command(BaseCommand):
    help = "Create tenant schools GVS001/BRS001 and reassign demo admins/teachers/students"

    def add_arguments(self, parser):
        parser.add_argument("--skip-migrate", action="store_true", help="Skip migrate_schemas (run separately)")

    def handle(self, *args, **options):
        skip_migrate = options["skip_migrate"]

        for spec in SCHOOLS_SPEC:
            school, created = School.objects.get_or_create(
                code=spec["code"],
                defaults={
                    "name": spec["name"],
                    "schema_name": spec["schema_name"],
                    "address": "",
                },
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created school: {spec['name']} ({spec['code']})"))
            else:
                self.stdout.write(f"School {spec['code']} already exists.")

            # Reassign users by prefix
            updated = User.objects.filter(
                username__startswith=f"{spec['prefix']}_",
                school__isnull=True,
            ).update(school=school)
            if updated:
                self.stdout.write(self.style.SUCCESS(f"Assigned {updated} users to {spec['code']}"))

        if not skip_migrate:
            self.stdout.write("Running migrate_schemas for tenant schemas...")
            from django.core.management import call_command
            call_command("migrate_schemas")

        self.stdout.write(self.style.SUCCESS("Done. gvs_admin1 etc. should now have school set."))
