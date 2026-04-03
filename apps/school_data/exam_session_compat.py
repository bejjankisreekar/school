"""
Create ExamSession rows when tenant DB predates updated_at / display_order columns.

ORM INSERT always includes model fields; defer() does not help on INSERT.
"""

from __future__ import annotations

from django.db import connection
from django.db.utils import ProgrammingError
from django.utils import timezone

# Only cache the "ORM insert is safe" case so adding columns via migrate is picked up
# on the next create without a process restart.
_orm_examsession_insert_ok: dict[str, bool] = {}


def _connection_cache_key() -> str:
    return getattr(connection, "schema_name", None) or str(connection.alias)


def _table_has_column(table_name: str, column_name: str) -> bool:
    with connection.cursor() as cursor:
        try:
            desc = connection.introspection.get_table_description(cursor, table_name)
        except Exception:
            return True
        return any(getattr(col, "name", None) == column_name for col in desc)


def _examsession_needs_raw_insert() -> bool:
    from .models import ExamSession

    key = _connection_cache_key()
    if _orm_examsession_insert_ok.get(key):
        return False
    if not _table_has_column(ExamSession._meta.db_table, "updated_at"):
        return True
    _orm_examsession_insert_ok[key] = True
    return False


def _examsession_raw_insert(
    *,
    name: str,
    class_name: str = "",
    section: str = "",
    classroom,
    created_by,
):
    from .models import ExamSession

    tbl = ExamSession._meta.db_table
    now = timezone.now()
    classroom_id = classroom.pk if classroom is not None else None
    cols = ["name", "class_name", "section", "classroom_id", "created_by_id", "created_at"]
    vals = [str(name)[:100], class_name or "", section or "", classroom_id, created_by.pk, now]
    qtbl = connection.ops.quote_name(tbl)
    qcols = ", ".join(connection.ops.quote_name(c) for c in cols)
    ph = ", ".join(["%s"] * len(vals))
    sql = f"INSERT INTO {qtbl} ({qcols}) VALUES ({ph})"
    with connection.cursor() as cur:
        if connection.vendor == "postgresql":
            sql += f" RETURNING {connection.ops.quote_name('id')}"
            cur.execute(sql, vals)
            pk = cur.fetchone()[0]
        else:
            cur.execute(sql, vals)
            pk = cur.lastrowid
    return ExamSession.objects.defer("updated_at", "display_order").get(pk=pk)


def examsession_create_compat(
    *,
    name: str,
    class_name: str = "",
    section: str = "",
    classroom,
    created_by,
):
    from .models import ExamSession

    kwargs = dict(
        name=name,
        class_name=class_name or "",
        section=section or "",
        classroom=classroom,
        created_by=created_by,
    )
    if _examsession_needs_raw_insert():
        return _examsession_raw_insert(**kwargs)
    try:
        return ExamSession.objects.create(**kwargs)
    except ProgrammingError as exc:
        connection.rollback()
        msg = str(exc).lower()
        if "updated_at" not in msg and "display_order" not in msg:
            raise
        return _examsession_raw_insert(**kwargs)
