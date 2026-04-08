import os
import sys
from pathlib import Path


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "school_erp_demo.settings")
    import django  # noqa: WPS433

    django.setup()

    from django.db import connection  # noqa: WPS433
    from django_tenants.utils import schema_context  # noqa: WPS433

    with schema_context("public"):
        with connection.cursor() as c:
            c.execute(
                "ALTER TABLE customers_school "
                "ADD COLUMN IF NOT EXISTS timetable_current_profile_id bigint NULL;"
            )

    print("OK: customers_school.timetable_current_profile_id ensured (public schema).")


if __name__ == "__main__":
    main()

