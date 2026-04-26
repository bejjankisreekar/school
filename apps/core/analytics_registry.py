"""
Platform-wide analytics field definitions (``AnalyticsField`` model).

Stored only in the PostgreSQL **public** schema. Always query through these helpers so
tenant-bound requests never read/write the wrong schema.
"""
from __future__ import annotations

from django_tenants.utils import get_public_schema_name, schema_context

from apps.core.models import AnalyticsField


def list_analytics_fields():
    """Return all registry rows (materialized while connected to the public schema)."""
    with schema_context(get_public_schema_name()):
        return list(
            AnalyticsField.objects.order_by(
                "category", "field_key", "display_order", "display_label", "id"
            )
        )


def count_analytics_fields() -> int:
    with schema_context(get_public_schema_name()):
        return AnalyticsField.objects.count()
