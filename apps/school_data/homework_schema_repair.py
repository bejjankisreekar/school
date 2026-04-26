"""
Repair school_data_homework when tenant DB is missing enterprise columns (migration 0045+).

PostgreSQL only. Idempotent (ADD COLUMN IF NOT EXISTS).
"""
from __future__ import annotations

# One statement per execute — some drivers only run the first statement in a multi-statement string.
_HOMEWORK_ALTER_STEPS: tuple[str, ...] = (
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS assigned_date date NULL",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS homework_type varchar(50) NOT NULL DEFAULT 'HOMEWORK'",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS max_marks smallint NULL",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS submission_type varchar(50) NOT NULL DEFAULT 'NOTEBOOK'",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS allow_late_submission boolean NOT NULL DEFAULT false",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS late_submission_until timestamp with time zone NULL",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS priority varchar(20) NOT NULL DEFAULT 'NORMAL'",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS status varchar(20) NOT NULL DEFAULT 'PUBLISHED'",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS academic_year_id bigint NULL",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS estimated_duration_minutes integer NULL",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS instructions text NOT NULL DEFAULT ''",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS submission_required boolean NOT NULL DEFAULT true",
)

_HOMEWORK_AUDIT_ALTER_STEPS: tuple[str, ...] = (
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS created_by_id bigint NULL",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS created_on timestamp with time zone NULL",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS modified_by_id bigint NULL",
    "ALTER TABLE school_data_homework ADD COLUMN IF NOT EXISTS modified_on timestamp with time zone NULL",
)

_BACKFILL_ASSIGNED_DATE = """
UPDATE school_data_homework
SET assigned_date = (created_at)::date
WHERE assigned_date IS NULL AND created_at IS NOT NULL
"""

_BACKFILL_CREATED_ON = """
UPDATE school_data_homework
SET created_on = created_at
WHERE created_on IS NULL AND created_at IS NOT NULL
"""

_BACKFILL_MODIFIED_ON = """
UPDATE school_data_homework
SET modified_on = created_at
WHERE modified_on IS NULL AND created_at IS NOT NULL
"""


def homework_enterprise_columns_ok(connection) -> bool:
    if connection.vendor != "postgresql":
        return True
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'school_data_homework'
              AND column_name = 'academic_year_id'
            """
        )
        return cursor.fetchone() is not None


def ensure_homework_enterprise_columns(connection) -> None:
    """Apply all enterprise columns + backfill assigned_date (PostgreSQL)."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = 'school_data_homework'
            """
        )
        if not cursor.fetchone():
            return
        for sql in _HOMEWORK_ALTER_STEPS:
            cursor.execute(sql)
        cursor.execute(_BACKFILL_ASSIGNED_DATE)


def ensure_homework_enterprise_columns_if_missing(connection) -> None:
    """Fast no-op when schema is already aligned; otherwise runs DDL."""
    if homework_enterprise_columns_ok(connection):
        return
    ensure_homework_enterprise_columns(connection)


def homework_audit_columns_ok(connection) -> bool:
    if connection.vendor != "postgresql":
        return True
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'school_data_homework'
              AND column_name = 'modified_by_id'
            """
        )
        return cursor.fetchone() is not None


def ensure_homework_audit_columns(connection) -> None:
    """Add audit columns used by BaseModel on Homework (PostgreSQL)."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = 'school_data_homework'
            """
        )
        if not cursor.fetchone():
            return
        for sql in _HOMEWORK_AUDIT_ALTER_STEPS:
            cursor.execute(sql)
        cursor.execute(_BACKFILL_CREATED_ON)
        cursor.execute(_BACKFILL_MODIFIED_ON)


def ensure_homework_audit_columns_if_missing(connection) -> None:
    """Fast no-op when schema is already aligned; otherwise runs DDL."""
    if homework_audit_columns_ok(connection):
        return
    ensure_homework_audit_columns(connection)
