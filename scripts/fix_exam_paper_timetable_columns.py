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
    from django_tenants.utils import get_tenant_model, schema_context  # noqa: WPS433

    Tenant = get_tenant_model()
    schemas = list(Tenant.objects.values_list("schema_name", flat=True))

    updated = 0
    skipped = 0
    for schema in schemas:
        with schema_context(schema):
            with connection.cursor() as c:
                try:
                    c.execute(
                        "ALTER TABLE school_data_exam "
                        "ADD COLUMN IF NOT EXISTS room varchar(120) NOT NULL DEFAULT '';"
                    )
                    c.execute(
                        "ALTER TABLE school_data_exam "
                        "ADD COLUMN IF NOT EXISTS details text NOT NULL DEFAULT '';"
                    )
                    c.execute(
                        "ALTER TABLE school_data_exam "
                        "ADD COLUMN IF NOT EXISTS topics text NOT NULL DEFAULT '';"
                    )
                    updated += 1
                except Exception:
                    skipped += 1

    print(f"Updated schemas: {updated} | Skipped schemas: {skipped}")


if __name__ == "__main__":
    main()

