"""
Ensure public table core_sidebarmenuitem exists when migrations are blocked.

Run: python manage.py ensure_sidebar_menu_table
"""

from django.core.management.base import BaseCommand

from apps.core.sidebar_storage import (
    ensure_sidebar_menu_storage,
    record_core_sidebar_migration_if_missing,
)


class Command(BaseCommand):
    help = "Ensure public table core_sidebarmenuitem exists (role-based sidebar menu)."

    def handle(self, *args, **options):
        ok = ensure_sidebar_menu_storage()
        if ok:
            record_core_sidebar_migration_if_missing()
            self.stdout.write(self.style.SUCCESS("Sidebar menu storage is ready."))
        else:
            self.stdout.write(self.style.ERROR("Could not ensure sidebar menu storage. Check DB connectivity/logs."))

