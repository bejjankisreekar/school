"""
Ensure public table core_schoolenrollmentrequest exists and matches the current model.
Safe to re-run. Also records core.0019 / 0020 in django_migrations when missing.

Run: python manage.py ensure_school_enrollment_table
"""
from django.core.management.base import BaseCommand

from apps.core.enrollment_storage import (
    ensure_school_enrollment_storage,
    record_core_migrations_if_missing,
)


class Command(BaseCommand):
    help = "Ensure public table core_schoolenrollmentrequest exists (enroll form)."

    def handle(self, *args, **options):
        ok = ensure_school_enrollment_storage()
        if ok:
            record_core_migrations_if_missing()
            self.stdout.write(self.style.SUCCESS("Enrollment storage is ready. /enroll/ can save requests."))
        else:
            self.stdout.write(
                self.style.ERROR(
                    "Could not ensure enrollment storage. Check database connectivity and logs."
                )
            )
