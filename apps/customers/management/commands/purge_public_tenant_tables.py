"""
Remove tenant-only tables mistakenly created in the public schema.

django-tenants keeps academic data in each school schema. If ``school_data_*`` (or
``timetable_*``, ``payroll_*``) exist in ``public``, PostgreSQL can resolve ORM
queries to those tables whenever the active tenant schema lacks the same table
or search_path falls through — causing cross-school "ghost" sections/classes/subjects.

Default is dry-run. Use --execute to DROP tables.

  python manage.py purge_public_tenant_tables
  python manage.py purge_public_tenant_tables --execute

Optional: remove bogus migration rows for tenant apps from public.django_migrations:

  python manage.py purge_public_tenant_tables --execute --clean-django-migrations
"""
from django.core.management.base import BaseCommand
from django.db import connection
from django_tenants.utils import get_public_schema_name


class Command(BaseCommand):
    help = "List or DROP tenant-app tables that must not live in the public schema."

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Actually DROP tables (default: dry-run only).",
        )
        parser.add_argument(
            "--clean-django-migrations",
            action="store_true",
            help="Also DELETE django_migrations rows for tenant apps on public (fixes bad migrate history).",
        )

    def handle(self, *args, **options):
        do_drop = options["execute"]
        clean_mig = options["clean_django_migrations"]
        public = get_public_schema_name()

        connection.set_schema_to_public()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = %s
                  AND (
                      tablename LIKE 'school_data_%%'
                      OR tablename LIKE 'timetable_%%'
                      OR tablename LIKE 'payroll_%%'
                  )
                ORDER BY tablename
                """,
                [public],
            )
            tables = [row[0] for row in cursor.fetchall()]

        if not tables:
            self.stdout.write(
                self.style.SUCCESS(
                    f"No tenant-only tables found in {public!r} — nothing to purge."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Found {len(tables)} table(s) in {public!r} that belong in tenant schemas only:"
                )
            )
            for t in tables:
                self.stdout.write(f"  - {t}")
            if not do_drop:
                self.stdout.write(
                    self.style.WARNING(
                        "Dry-run only. Re-run with --execute to DROP these tables (CASCADE)."
                    )
                )
            else:
                with connection.cursor() as cursor:
                    for t in tables:
                        # Identifier must be quoted; names are lowercase from Django.
                        cursor.execute(
                            f'DROP TABLE IF EXISTS "{public}"."{t}" CASCADE'
                        )
                        self.stdout.write(self.style.SUCCESS(f"  Dropped {public}.{t}"))
                self.stdout.write(
                    self.style.SUCCESS(
                        "Run migrate_schemas for each school if any tenant was missing tables."
                    )
                )

        if clean_mig and do_drop:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM django_migrations
                    WHERE app IN ('school_data', 'timetable', 'payroll')
                    """
                )
                n = cursor.rowcount
            self.stdout.write(
                self.style.SUCCESS(
                    f"Removed {n} django_migrations row(s) for tenant apps on public."
                )
            )
        elif clean_mig and not do_drop:
            self.stdout.write(
                self.style.WARNING(
                    "--clean-django-migrations requires --execute (same run)."
                )
            )
