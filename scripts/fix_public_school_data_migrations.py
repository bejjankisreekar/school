import os
import sys
from pathlib import Path


def main() -> None:
    # Ensure project root is on sys.path when running from /scripts.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "school_erp_demo.settings")
    import django

    django.setup()
    from django.db import connection
    from django.utils import timezone

    tenant_apps = ["school_data", "timetable", "payroll"]
    total = 0
    with connection.cursor() as cur:
        for app in tenant_apps:
            cur.execute("DELETE FROM django_migrations WHERE app = %s", [app])
            total += cur.rowcount
            print(f"Deleted {cur.rowcount} public migration rows for {app}.")
    print(f"Deleted {total} total public migration rows for tenant apps.")

    # Some shared-schema migrations historically depended on tenant app migrations.
    # To keep public migration history consistent (without creating tenant tables in public),
    # we fake-insert the specific dependency rows if missing.
    required_rows = [
        ("timetable", "0004_alter_timeslot_options_remove_timeslot_school_and_more"),
    ]
    with connection.cursor() as cur:
        for app, name in required_rows:
            cur.execute(
                "SELECT 1 FROM django_migrations WHERE app=%s AND name=%s",
                [app, name],
            )
            if cur.fetchone():
                continue
            cur.execute(
                "INSERT INTO django_migrations (app, name, applied) VALUES (%s, %s, %s)",
                [app, name, timezone.now()],
            )
            print(f"Inserted public migration row: {app}.{name}")


if __name__ == "__main__":
    main()

