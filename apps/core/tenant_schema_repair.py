"""
Repair tenant PostgreSQL schemas that exist but never received tenant-app migrations.

Typical symptom: ProgrammingError relation "school_data_*" does not exist.
"""
from __future__ import annotations

import logging

from django.core.management import call_command
from django.db import connection, connections

logger = logging.getLogger(__name__)


def tenant_schema_repair_may_run_migrate() -> bool:
    """
    When False (default in production), views do not call migrate_schemas on errors.

    Deploy workflow: after releasing code that adds/changes TENANT_APPS migrations, run:
        python manage.py migrate_schemas
    Or target one tenant: migrate_schemas -s <schema_name> --tenant
    """
    from django.conf import settings

    return bool(getattr(settings, "TENANT_LAZY_SCHEMA_REPAIR", True))


def _iter_linked_exceptions(exc: BaseException):
    """Walk __cause__ and __context__ (cycle-safe). Django often links ProgrammingError via __context__ when cursor.close() raises."""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        yield cur
        c = getattr(cur, "__cause__", None)
        if c is not None:
            stack.append(c)
        ctx = getattr(cur, "__context__", None)
        if ctx is not None:
            stack.append(ctx)


def tenant_missing_school_data_relation_error(exc: BaseException) -> bool:
    """True if this or a linked exception is a missing school_data_* table/relation."""
    for cur in _iter_linked_exceptions(exc):
        msg = str(cur).lower()
        compact = msg.replace(" ", "")
        # PostgreSQL: relation "school_data_academicyear" does not exist
        if "school_data" in msg and (
            "does not exist" in msg or "undefinedtable" in compact
        ):
            return True
    return False


def _django_named_cursor_does_not_exist(exc: BaseException) -> bool:
    """Chunked server-side cursor invalidated (often after a failed query or schema switch)."""
    for cur in _iter_linked_exceptions(exc):
        msg = str(cur).lower()
        if "_django_curs" in msg and "does not exist" in msg:
            return True
    return False


def tenant_schema_repair_should_retry(exc: BaseException) -> bool:
    """
    True if we should run migrate_schemas for the tenant and retry the view.

    Includes missing school_data_* relations. InvalidCursorName from closing a named
    cursor is often chained on ProgrammingError via __context__ (not __cause__), so we
    walk both. Bare _django_curs* errors also get a migrate + connection close + retry.

    Gated by settings.TENANT_LAZY_SCHEMA_REPAIR (off in production by default).
    """
    would_retry = tenant_missing_school_data_relation_error(exc) or _django_named_cursor_does_not_exist(exc)
    if not tenant_schema_repair_may_run_migrate():
        if would_retry:
            schema = "?"
            try:
                t = getattr(connection, "tenant", None)
                if t is not None:
                    schema = getattr(t, "schema_name", "?") or "?"
            except Exception:
                pass
            logger.error(
                "Tenant DB repair needed (schema=%s) but TENANT_LAZY_SCHEMA_REPAIR is disabled; "
                "run migrate_schemas at deploy (see settings.TENANT_LAZY_SCHEMA_REPAIR).",
                schema,
            )
        return False
    return would_retry


def programming_error_missing_academic_year_table(exc: BaseException) -> bool:
    """Backward-compatible alias for tenant_missing_school_data_relation_error."""
    return tenant_missing_school_data_relation_error(exc)


def _close_tenant_database_connection() -> None:
    """Close the tenant DB handle so the next request gets a clean connection after migrate."""
    try:
        from django_tenants.utils import get_tenant_database_alias

        connections[get_tenant_database_alias()].close()
    except Exception:
        pass


# Minimum tenant tables for school admin student add (avoid probing every school_data_* table).
_CORE_SCHOOL_DATA_TABLES = (
    "school_data_academicyear",
    "school_data_classroom",
    "school_data_section",
)


def ensure_core_school_data_tables_if_needed(request, school) -> None:
    """
    Probe whether the tenant schema is missing core ``school_data_*`` tables.

    Production safety: **do not run migrations inside requests**. Missing tables must be
    fixed out-of-band via `python manage.py migrate_schemas` (or per-tenant).

    We keep this probe to (a) short-circuit risky request paths and (b) produce clear logs.
    """
    if getattr(request, "_core_school_data_table_check", False):
        return
    from django_tenants.utils import get_public_schema_name

    schema = getattr(school, "schema_name", None)
    public = get_public_schema_name()
    if not schema or schema == public:
        request._core_school_data_table_check = True
        return

    def _table_exists(table: str) -> bool:
        from django_tenants.utils import get_tenant_database_alias

        conn = connections[get_tenant_database_alias()]
        conn.ensure_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE LOWER(table_schema) = LOWER(%s) AND table_name = %s
                LIMIT 1
                """,
                [schema, table],
            )
            return cur.fetchone() is not None

    def _all_core_tables_exist() -> bool:
        return all(_table_exists(t) for t in _CORE_SCHOOL_DATA_TABLES)

    try:
        if _all_core_tables_exist():
            request._core_school_data_table_check = True
            return
    except Exception:
        logger.exception("information_schema probe failed (schema=%s)", schema)
        return

    # Report missing tables; do NOT attempt to migrate in-request.
    missing = []
    for t in _CORE_SCHOOL_DATA_TABLES:
        try:
            if not _table_exists(t):
                missing.append(t)
        except Exception:
            missing.append(t)
    setattr(request, "_core_school_data_missing_tables", tuple(sorted(set(missing))))
    logger.error(
        "Core tenant tables missing (schema=%s missing=%s). "
        "Run: python manage.py migrate_schemas -s %s",
        schema,
        ",".join(getattr(request, "_core_school_data_missing_tables", ())),
        schema,
    )

    request._core_school_data_table_check = True


def apply_tenant_migrations_for_school(school, *, verbosity: int = 0) -> None:
    """
    Run migrate_schemas for one school tenant (TENANT_APPS only). Idempotent.

    Single canonical migration step for a schema; used when the PostgreSQL schema
    already existed but django-tenants skipped migrations (empty/partial tenant).
    """
    from django_tenants.utils import get_public_schema_name

    schema = getattr(school, "schema_name", None)
    public = get_public_schema_name()
    if not schema or schema == public:
        return
    logger.info("migrate_schemas for tenant schema=%s", schema)
    try:
        call_command(
            "migrate_schemas",
            schema_name=schema,
            tenant=True,
            interactive=False,
            verbosity=verbosity,
        )
    except Exception:
        logger.exception("migrate_schemas failed (schema=%s)", schema)
        _close_tenant_database_connection()
        raise
    _close_tenant_database_connection()


def run_migrate_schemas_for_tenant_school(school) -> None:
    """
    Ensure schema exists and tenant migrations are applied (repair / lazy safety net).

    Delegates to School.create_schema, which runs migrations when the schema pre-existed
    without tables (see School.create_schema override).
    """
    if not tenant_schema_repair_may_run_migrate():
        logger.warning(
            "run_migrate_schemas_for_tenant_school skipped (TENANT_LAZY_SCHEMA_REPAIR is False)"
        )
        return
    from django_tenants.utils import get_public_schema_name, schema_exists

    schema = getattr(school, "schema_name", None)
    public = get_public_schema_name()
    if not schema or schema == public:
        return
    if not schema_exists(schema):
        logger.warning("Creating missing PostgreSQL schema before migrate: %s", schema)
    logger.warning("Ensuring tenant migrations (schema=%s)", schema)
    school.create_schema(check_if_exists=True, sync_schema=True, verbosity=0)


def recover_db_connection_for_request(request, *, close_connection: bool = True) -> None:
    # IMPORTANT: in django-tenants, the tenant connection may not be the default alias.
    # Always operate on the tenant DB alias connection to clear aborted transactions.
    from django_tenants.utils import get_tenant_database_alias

    alias = get_tenant_database_alias()
    conn = connections[alias]
    try:
        # Be defensive: after any DB error PostgreSQL marks the transaction aborted
        # until an explicit ROLLBACK. Django's needs_rollback flag is not reliable
        # across all failure/alias paths in this project, so always attempt rollback.
        conn.rollback()
    except Exception:
        pass
    if close_connection:
        try:
            conn.close()
        except Exception:
            pass
    from apps.core.tenant_scope import ensure_tenant_for_request

    # After a close() Django will reconnect lazily, but set_tenant() and immediate ORM
    # in the same call stack can hit "connection already closed" on some failure paths.
    try:
        conn.ensure_connection()
    except Exception:
        return

    try:
        ensure_tenant_for_request(request)
    except Exception:
        # Never let request recovery raise a second exception.
        return
