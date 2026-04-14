"""
Create notifications tables on the public schema when migrations did not run.

``apps.notifications`` is in SHARED_APPS; these tables must exist in ``public``.
FKs to ``school_data`` are omitted here because those tables live in tenant schemas only.
"""

from django.db import connection


def ensure_notifications_public_tables() -> list[str]:
    """
    Run idempotent CREATE TABLE IF NOT EXISTS for all notifications models that
    reference ``customers.School`` (needed for CASCADE on tenant rollback / School.delete).

    Returns human-readable actions taken (e.g. ["created notifications_notificationtemplate"]).
    """
    if connection.vendor != "postgresql":
        return []

    actions: list[str] = []

    def _exists(table: str) -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = current_schema() AND table_name = %s
                """,
                [table],
            )
            return cursor.fetchone() is not None

    # One SQL string per execute (psycopg2).
    stmts: list[tuple[str, list[str]]] = [
        (
            "notifications_notificationtemplate",
            [
                """
                CREATE TABLE IF NOT EXISTS notifications_notificationtemplate (
                    id BIGSERIAL PRIMARY KEY,
                    school_id BIGINT NULL REFERENCES customers_school(id) ON DELETE CASCADE,
                    name VARCHAR(100) NOT NULL,
                    code VARCHAR(50) NOT NULL,
                    channel VARCHAR(10) NOT NULL,
                    subject VARCHAR(200) NOT NULL DEFAULT '',
                    body TEXT NOT NULL
                )
                """,
                """
                CREATE UNIQUE INDEX IF NOT EXISTS notifications_notificationtemplate_code_key
                    ON notifications_notificationtemplate (code)
                """,
            ],
        ),
        (
            "notifications_schoolsmscredit",
            [
                """
                CREATE TABLE IF NOT EXISTS notifications_schoolsmscredit (
                    id BIGSERIAL PRIMARY KEY,
                    school_id BIGINT NOT NULL UNIQUE REFERENCES customers_school(id) ON DELETE CASCADE,
                    balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """,
            ],
        ),
        (
            "notifications_notificationlog",
            [
                """
                CREATE TABLE IF NOT EXISTS notifications_notificationlog (
                    id BIGSERIAL PRIMARY KEY,
                    school_id BIGINT NOT NULL REFERENCES customers_school(id) ON DELETE CASCADE,
                    sender_id BIGINT NULL REFERENCES accounts_user(id) ON DELETE SET NULL,
                    sender_role VARCHAR(20) NOT NULL,
                    channel VARCHAR(10) NOT NULL,
                    target_type VARCHAR(20) NOT NULL,
                    target_class_id BIGINT NULL,
                    target_section_id BIGINT NULL,
                    target_student_id BIGINT NULL,
                    recipient_name VARCHAR(255) NOT NULL DEFAULT '',
                    recipient_phone VARCHAR(20) NOT NULL DEFAULT '',
                    recipient_email VARCHAR(254) NOT NULL DEFAULT '',
                    subject VARCHAR(255) NOT NULL DEFAULT '',
                    body TEXT NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sent_at TIMESTAMPTZ NULL
                )
                """,
            ],
        ),
        (
            "notifications_studentnotificationread",
            [
                """
                CREATE TABLE IF NOT EXISTS notifications_studentnotificationread (
                    id BIGSERIAL PRIMARY KEY,
                    student_id BIGINT NOT NULL,
                    notification_id BIGINT NOT NULL REFERENCES notifications_notificationlog(id) ON DELETE CASCADE,
                    read_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT unique_student_notification_read UNIQUE (student_id, notification_id)
                )
                """,
            ],
        ),
    ]

    with connection.cursor() as cursor:
        for table, sql_parts in stmts:
            before = _exists(table)
            for sql in sql_parts:
                cursor.execute(sql)
            if not before and _exists(table):
                actions.append(f"created {table}")

    return actions
