"""
Tenant schema DB utilities (safe for schema-per-tenant Postgres).

These helpers are intentionally small and dependency-free so they can be reused from
views, middleware, and management commands.
"""

from __future__ import annotations

import logging
from typing import Iterable

from django.db import connections
from django_tenants.utils import get_tenant_database_alias

logger = logging.getLogger(__name__)


def table_exists(schema_name: str, table_name: str, *, using: str | None = None) -> bool:
    """
    Return True if `schema_name.table_name` exists.

    Uses `information_schema.tables` (stable across PG versions). This does NOT depend
    on search_path, so it is safe even when the current connection search_path is wrong.
    """
    if not schema_name or not table_name:
        return False
    alias = using or get_tenant_database_alias()
    conn = connections[alias]
    try:
        # Be defensive: in some error paths the connection may have been closed.
        conn.ensure_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE LOWER(table_schema) = LOWER(%s) AND table_name = %s
                LIMIT 1
                """,
                [schema_name, table_name],
            )
            return cur.fetchone() is not None
    except Exception:
        logger.exception("table_exists probe failed (schema=%s table=%s alias=%s)", schema_name, table_name, alias)
        return False


def missing_tables(schema_name: str, tables: Iterable[str], *, using: str | None = None) -> list[str]:
    """Return list of missing table names for the given schema."""
    missing: list[str] = []
    for t in tables:
        if not table_exists(schema_name, t, using=using):
            missing.append(t)
    return missing


def tenant_school_data_core_ready(school) -> bool:
    """
    True if trivial ORM reads succeed inside ``school``'s PostgreSQL schema.

    Prefer this over ``information_schema`` probes alone: after connection errors or
    ATOMIC_REQUESTS rollbacks, ``table_exists`` can false-negative while the tenant is fine.
    """
    if school is None or not getattr(school, "schema_name", None):
        return False
    from django_tenants.utils import get_public_schema_name, tenant_context

    if school.schema_name == get_public_schema_name():
        return False
    try:
        from apps.school_data.models import AcademicYear, Section

        with tenant_context(school):
            list(AcademicYear.objects.only("pk")[:1])
            list(Section.objects.only("pk")[:1])
        return True
    except Exception as exc:
        logger.warning(
            "tenant_school_data_core_ready failed schema=%s err=%s",
            getattr(school, "schema_name", ""),
            exc,
        )
        return False

