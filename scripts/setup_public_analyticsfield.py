from __future__ import annotations

from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django_tenants.utils import schema_context


def main() -> None:
    with schema_context("public"):
        with connection.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS core_analyticsfield (
                  id bigserial PRIMARY KEY,
                  created_on timestamptz NOT NULL DEFAULT now(),
                  modified_on timestamptz NOT NULL DEFAULT now(),
                  created_by_id bigint NULL,
                  modified_by_id bigint NULL,
                  field_key varchar(80) NOT NULL,
                  display_label varchar(160) NOT NULL,
                  category varchar(60) NOT NULL DEFAULT '',
                  display_order integer NOT NULL DEFAULT 0,
                  is_active boolean NOT NULL DEFAULT true
                );
                """
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uniq_analyticsfield_key_label "
                "ON core_analyticsfield(field_key, display_label);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS core_analyticsfield_field_key_idx "
                "ON core_analyticsfield(field_key);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS core_analyticsfield_category_idx "
                "ON core_analyticsfield(category);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS core_analyticsfield_display_order_idx "
                "ON core_analyticsfield(display_order);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS core_analyticsfield_is_active_idx "
                "ON core_analyticsfield(is_active);"
            )

        # Record the migration as applied in PUBLIC schema to keep Django consistent.
        if not MigrationRecorder.Migration.objects.filter(app="core", name="0022_analyticsfield").exists():
            MigrationRecorder.Migration.objects.create(app="core", name="0022_analyticsfield")

    print("PUBLIC core_analyticsfield ready.")


if __name__ == "__main__":
    main()

