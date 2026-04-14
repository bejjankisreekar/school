"""
Ensure notifications tables exist on the public schema (SHARED_APPS).

Run when ``create_demo_users`` or tenant School save fails with missing
``notifications_notificationtemplate`` (or related) relation.

    python manage.py ensure_notifications_public_tables
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django_tenants.utils import get_public_schema_name, schema_context

from apps.notifications.db_bootstrap import ensure_notifications_public_tables


class Command(BaseCommand):
    help = "Creates notifications_* tables on public schema if missing (PostgreSQL)."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stderr.write(self.style.ERROR("PostgreSQL only."))
            return

        public = get_public_schema_name()
        with schema_context(public):
            actions = ensure_notifications_public_tables()
            if actions:
                for line in actions:
                    self.stdout.write(self.style.SUCCESS(line))
            else:
                self.stdout.write("Notifications public tables already present.")

            recorder = MigrationRecorder(connection)
            applied = recorder.applied_migrations()
            for name in ("0001_initial", "0002_studentnotificationread"):
                key = ("notifications", name)
                if key not in applied:
                    try:
                        recorder.record_applied(*key)
                        self.stdout.write(self.style.SUCCESS(f"Recorded migrations {name}."))
                    except Exception as exc:
                        self.stdout.write(
                            self.style.WARNING(
                                f"Could not record {name} in django_migrations ({exc}). "
                                f"You may need: python manage.py migrate notifications {name} --fake"
                            )
                        )

        self.stdout.write(self.style.SUCCESS("Done."))
