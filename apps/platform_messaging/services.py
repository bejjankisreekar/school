from __future__ import annotations

import logging
from typing import Any

from django.db import connection, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from apps.customers.models import School

from .models import PlatformMessage, PlatformMessageThread

logger = logging.getLogger(__name__)


def ensure_public_schema() -> None:
    connection.set_schema_to_public()


def resolve_school_pk_for_user(user) -> int | None:
    """
    For school admins, ``User.school`` uses ``to_field='code'``, so ``user.school_id`` is the
    school *code* (e.g. ABC123), not ``School.pk``. Platform messaging always keys threads by
    integer primary key.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return None
    code_or_empty = getattr(user, "school_id", None)
    if code_or_empty in (None, ""):
        return None
    sch = getattr(user, "school", None)
    if sch is not None:
        pk = getattr(sch, "pk", None)
        if pk is not None:
            return int(pk)
    ensure_public_schema()
    row = School.objects.filter(code=code_or_empty).values_list("pk", flat=True).first()
    return int(row) if row is not None else None


def get_or_create_thread(school_id: int) -> PlatformMessageThread:
    ensure_public_schema()
    school = School.objects.exclude(schema_name="public").get(pk=school_id)
    thread, _ = PlatformMessageThread.objects.get_or_create(school=school)
    return thread


def unread_count_for_superadmin() -> int:
    ensure_public_schema()
    return int(
        PlatformMessage.objects.filter(
            sender_role=PlatformMessage.SenderRole.SCHOOLADMIN,
            read_at__isnull=True,
            thread__archived_by_superadmin=False,
        ).count()
    )


def unread_count_for_school_admin(school_id: int | None) -> int:
    if not school_id:
        return 0
    ensure_public_schema()
    return int(
        PlatformMessage.objects.filter(
            thread__school_id=school_id,
            sender_role=PlatformMessage.SenderRole.SUPERADMIN,
            read_at__isnull=True,
            thread__archived_by_school=False,
        ).count()
    )


def list_threads_superadmin(
    *,
    filter_kind: str = "all",
    q: str = "",
    page: int = 1,
    page_size: int = 40,
) -> dict[str, Any]:
    ensure_public_schema()
    filter_kind = (filter_kind or "all").lower()
    q = (q or "").strip()

    school_qs = School.objects.exclude(schema_name="public").select_related("plan")
    if q:
        msg_match = PlatformMessage.objects.filter(
            thread__school_id=OuterRef("pk"),
            body__icontains=q,
        )
        school_qs = school_qs.filter(Q(name__icontains=q) | Q(Exists(msg_match)))

    thread_qs = (
        PlatformMessageThread.objects.select_related("school")
        .annotate(
            unread_school=Count(
                "messages",
                filter=Q(
                    messages__sender_role=PlatformMessage.SenderRole.SCHOOLADMIN,
                    messages__read_at__isnull=True,
                ),
            ),
        )
        .all()
    )
    thread_by_school = {t.school_id: t for t in thread_qs}

    rows: list[dict[str, Any]] = []
    for school in school_qs.order_by("name"):
        thread = thread_by_school.get(school.id)
        if filter_kind == "archived":
            if not thread or not thread.archived_by_superadmin:
                continue
        else:
            if thread and thread.archived_by_superadmin:
                continue

        last_msg = None
        unread = 0
        updated_at = school.created_on
        if thread:
            last_msg = thread.messages.order_by("-created_at").values("body", "created_at", "sender_role").first()
            unread = int(thread.unread_school)  # type: ignore[attr-defined]
            updated_at = thread.updated_at
            if filter_kind == "unread" and unread == 0:
                continue
        elif filter_kind in ("unread", "archived"):
            continue

        preview = ""
        ts = None
        if last_msg:
            preview = (last_msg["body"] or "")[:140]
            ts = last_msg["created_at"]

        rows.append(
            {
                "school_id": school.id,
                "school_name": school.name,
                "thread_id": thread.id if thread else None,
                "last_preview": preview,
                "last_message_at": ts.isoformat() if ts else None,
                "updated_at": updated_at.isoformat() if updated_at else None,
                "unread_count": unread,
                "pinned": bool(thread and thread.pinned_at),
                "archived": bool(thread and thread.archived_by_superadmin),
            }
        )

    rows = sorted(rows, key=lambda r: r["last_message_at"] or "", reverse=True)
    rows = sorted(rows, key=lambda r: 0 if r["pinned"] else 1)

    total = len(rows)
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    start = (page - 1) * page_size
    end = start + page_size

    return {"results": rows[start:end], "total": total, "page": page, "page_size": page_size}


def list_thread_school_admin(school_id: int, *, filter_kind: str = "all") -> dict[str, Any]:
    ensure_public_schema()
    filter_kind = (filter_kind or "all").lower()
    school = School.objects.get(pk=school_id)
    thread = PlatformMessageThread.objects.filter(school_id=school_id).first()
    unread = unread_count_for_school_admin(school_id)
    if filter_kind == "unread" and unread == 0:
        return {"results": [], "total": 0, "page": 1, "page_size": 1}
    if filter_kind == "archived":
        if not thread or not thread.archived_by_school:
            return {"results": [], "total": 0, "page": 1, "page_size": 1}

    last_msg = None
    if thread:
        last_msg = thread.messages.order_by("-created_at").values("body", "created_at", "sender_role").first()
    preview = ""
    ts = None
    if last_msg:
        preview = (last_msg["body"] or "")[:140]
        ts = last_msg["created_at"]
    return {
        "results": [
            {
                "school_id": school_id,
                "school_name": school.name,
                "thread_id": thread.id if thread else None,
                "last_preview": preview,
                "last_message_at": ts.isoformat() if ts else None,
                "unread_count": unread,
                "pinned": bool(thread and thread.pinned_at),
                "archived": bool(thread and thread.archived_by_school),
            }
        ],
        "total": 1,
        "page": 1,
        "page_size": 1,
    }


def get_messages(school_id: int, *, after_id: int | None = None) -> list[dict[str, Any]]:
    ensure_public_schema()
    thread = PlatformMessageThread.objects.filter(school_id=school_id).first()
    if not thread:
        return []
    qs = thread.messages.select_related("sender").order_by("created_at")
    if after_id:
        qs = qs.filter(pk__gt=after_id)
    out = []
    for m in qs:
        name = "Super Admin"
        if m.sender_role == PlatformMessage.SenderRole.SCHOOLADMIN:
            name = "School Admin"
        if m.sender:
            fn = m.sender.get_full_name()
            if fn.strip():
                name = fn
        out.append(
            {
                "id": m.pk,
                "sender_role": m.sender_role,
                "sender_name": name,
                "body": m.body,
                "created_at": m.created_at.isoformat(),
                "read_at": m.read_at.isoformat() if m.read_at else None,
            }
        )
    return out


def mark_read(school_id: int, *, reader_is_superadmin: bool) -> None:
    ensure_public_schema()
    thread = PlatformMessageThread.objects.filter(school_id=school_id).first()
    if not thread:
        return
    now = timezone.now()
    if reader_is_superadmin:
        thread.messages.filter(
            sender_role=PlatformMessage.SenderRole.SCHOOLADMIN,
            read_at__isnull=True,
        ).update(read_at=now)
    else:
        thread.messages.filter(
            sender_role=PlatformMessage.SenderRole.SUPERADMIN,
            read_at__isnull=True,
        ).update(read_at=now)


def send_message(
    *,
    school_id: int,
    body: str,
    user,
    sender_role: str,
) -> PlatformMessage:
    text = (body or "").strip()
    if not text:
        raise ValueError("empty_body")
    if len(text) > 8000:
        raise ValueError("body_too_long")

    ensure_public_schema()
    with transaction.atomic():
        thread = get_or_create_thread(school_id)
        msg = PlatformMessage.objects.create(
            thread=thread,
            sender_role=sender_role,
            sender=user,
            body=text,
        )
        thread.updated_at = timezone.now()
        thread.save(update_fields=["updated_at"])
    return msg


def set_thread_state(
    school_id: int,
    *,
    viewer_is_superadmin: bool,
    pinned: bool | None = None,
    archived: bool | None = None,
    mark_unread: bool = False,
) -> PlatformMessageThread:
    ensure_public_schema()
    thread = get_or_create_thread(school_id)
    fields: list[str] = []
    if viewer_is_superadmin:
        if pinned is not None:
            thread.pinned_at = timezone.now() if pinned else None
            fields.append("pinned_at")
        if archived is not None:
            thread.archived_by_superadmin = archived
            fields.append("archived_by_superadmin")
        if mark_unread:
            thread.messages.filter(sender_role=PlatformMessage.SenderRole.SCHOOLADMIN).update(read_at=None)
    else:
        if archived is not None:
            thread.archived_by_school = archived
            fields.append("archived_by_school")
        if mark_unread:
            thread.messages.filter(sender_role=PlatformMessage.SenderRole.SUPERADMIN).update(read_at=None)
    if fields:
        thread.save(update_fields=fields + ["updated_at"])
    return thread


def broadcast_message(body: str, user) -> int:
    text = (body or "").strip()
    if not text or len(text) > 8000:
        raise ValueError("invalid_body")
    ensure_public_schema()
    school_ids = list(School.objects.exclude(schema_name="public").values_list("id", flat=True))
    n = 0
    for sid in school_ids:
        try:
            send_message(
                school_id=sid,
                body=text,
                user=user,
                sender_role=PlatformMessage.SenderRole.SUPERADMIN,
            )
            n += 1
        except Exception:
            logger.exception("broadcast skip school_id=%s", sid)
    return n
