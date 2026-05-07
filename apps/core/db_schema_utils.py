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
                WHERE table_schema = %s AND table_name = %s
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

