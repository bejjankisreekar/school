"""
JSON APIs for Super Admin ↔ School Admin platform messaging (public schema).

Super Admin (prefix ``/super-admin/control-center/messages/api/``):

- ``GET threads/`` — list threads (``?filter=all|unread|archived`` ``&q=`` ``&page=``).
- ``GET school/<school_id>/messages/`` — messages for thread keyed by ``school_id`` (one thread per school).
- ``POST send/`` — body ``{"school_id", "message"}``.
- ``POST mark-read/`` — body ``{"school_id"}``.
- ``POST thread-state/`` — pin / archive / mark unread (body ``school_id`` + flags).
- ``POST broadcast/`` — body ``{"message"}`` to all schools.

School Admin (prefix ``/school-admin/messages/api/``): same semantics scoped to the logged-in admin's school.
"""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import superadmin_required
from apps.accounts.models import User
from apps.core.subscription_access import plan_access_denied_json

from .access import school_admin_can_use_platform_messaging
from .models import PlatformMessage
from . import services


def _json_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def _require_school_admin(request):
    user = request.user
    if not getattr(user, "is_authenticated", False):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=401)
    if getattr(user, "role", None) != User.Roles.ADMIN:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    sid = getattr(user, "school_id", None)
    if not sid:
        return JsonResponse({"ok": False, "error": "No school assigned"}, status=403)
    return None


def _school_platform_messaging_forbidden(request):
    if school_admin_can_use_platform_messaging(request.user):
        return None
    return plan_access_denied_json()


@superadmin_required
@require_GET
def sa_threads(request):
    services.ensure_public_schema()
    flt = (request.GET.get("filter") or "all").strip()
    q = (request.GET.get("q") or "").strip()
    try:
        page = int(request.GET.get("page") or "1")
    except ValueError:
        page = 1
    try:
        page_size = int(request.GET.get("page_size") or "40")
    except ValueError:
        page_size = 40
    data = services.list_threads_superadmin(filter_kind=flt, q=q, page=page, page_size=page_size)
    return JsonResponse({"ok": True, **data})


@superadmin_required
@require_GET
def sa_messages(request, school_id: int):
    services.ensure_public_schema()
    after_id = request.GET.get("after_id")
    try:
        after_id = int(after_id) if after_id else None
    except ValueError:
        after_id = None
    messages = services.get_messages(school_id, after_id=after_id)
    return JsonResponse({"ok": True, "school_id": school_id, "messages": messages})


@superadmin_required
@require_POST
def sa_send(request):
    services.ensure_public_schema()
    body = _json_body(request)
    school_id = body.get("school_id")
    text = body.get("message") or body.get("body") or ""
    try:
        school_id = int(school_id)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid school_id"}, status=400)
    try:
        msg = services.send_message(
            school_id=school_id,
            body=str(text),
            user=request.user,
            sender_role=PlatformMessage.SenderRole.SUPERADMIN,
        )
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    return JsonResponse(
        {
            "ok": True,
            "message": {
                "id": msg.pk,
                "sender_role": msg.sender_role,
                "body": msg.body,
                "created_at": msg.created_at.isoformat(),
                "read_at": None,
            },
        }
    )


@superadmin_required
@require_POST
def sa_mark_read(request):
    services.ensure_public_schema()
    body = _json_body(request)
    try:
        school_id = int(body.get("school_id"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid school_id"}, status=400)
    services.mark_read(school_id, reader_is_superadmin=True)
    return JsonResponse({"ok": True})


@superadmin_required
@require_POST
def sa_thread_state(request):
    services.ensure_public_schema()
    body = _json_body(request)
    try:
        school_id = int(body.get("school_id"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid school_id"}, status=400)
    pinned = body.get("pinned")
    if pinned is not None:
        pinned = bool(pinned)
    archived = body.get("archived")
    if archived is not None:
        archived = bool(archived)
    mark_unread = bool(body.get("mark_unread"))
    services.set_thread_state(
        school_id,
        viewer_is_superadmin=True,
        pinned=pinned,
        archived=archived,
        mark_unread=mark_unread,
    )
    return JsonResponse({"ok": True})


@superadmin_required
@require_POST
def sa_broadcast(request):
    services.ensure_public_schema()
    body = _json_body(request)
    text = body.get("message") or body.get("body") or ""
    try:
        n = services.broadcast_message(str(text), request.user)
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    return JsonResponse({"ok": True, "sent_to_schools": n})


# --- School admin (tenant) APIs ---


@login_required
@require_GET
def school_threads(request):
    err = _require_school_admin(request)
    if err:
        return err
    err = _school_platform_messaging_forbidden(request)
    if err:
        return err
    services.ensure_public_schema()
    flt = (request.GET.get("filter") or "all").strip()
    sid = services.resolve_school_pk_for_user(request.user)
    if sid is None:
        return JsonResponse({"ok": False, "error": "No school assigned"}, status=403)
    data = services.list_thread_school_admin(sid, filter_kind=flt)
    return JsonResponse({"ok": True, **data})


@login_required
@require_GET
def school_messages(request, school_id: int):
    err = _require_school_admin(request)
    if err:
        return err
    err = _school_platform_messaging_forbidden(request)
    if err:
        return err
    my_pk = services.resolve_school_pk_for_user(request.user)
    if my_pk is None or int(school_id) != int(my_pk):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    services.ensure_public_schema()
    after_id = request.GET.get("after_id")
    try:
        after_id = int(after_id) if after_id else None
    except ValueError:
        after_id = None
    messages = services.get_messages(school_id, after_id=after_id)
    return JsonResponse({"ok": True, "school_id": school_id, "messages": messages})


@login_required
@require_POST
def school_send(request):
    err = _require_school_admin(request)
    if err:
        return err
    err = _school_platform_messaging_forbidden(request)
    if err:
        return err
    services.ensure_public_schema()
    body = _json_body(request)
    school_id = services.resolve_school_pk_for_user(request.user)
    if school_id is None:
        return JsonResponse({"ok": False, "error": "No school assigned"}, status=403)
    text = body.get("message") or body.get("body") or ""
    try:
        msg = services.send_message(
            school_id=int(school_id),
            body=str(text),
            user=request.user,
            sender_role=PlatformMessage.SenderRole.SCHOOLADMIN,
        )
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    return JsonResponse(
        {
            "ok": True,
            "message": {
                "id": msg.pk,
                "sender_role": msg.sender_role,
                "body": msg.body,
                "created_at": msg.created_at.isoformat(),
                "read_at": None,
            },
        }
    )


@login_required
@require_POST
def school_mark_read(request):
    err = _require_school_admin(request)
    if err:
        return err
    services.ensure_public_schema()
    sid = services.resolve_school_pk_for_user(request.user)
    if sid is None:
        return JsonResponse({"ok": False, "error": "No school assigned"}, status=403)
    services.mark_read(sid, reader_is_superadmin=False)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def school_thread_state(request):
    err = _require_school_admin(request)
    if err:
        return err
    err = _school_platform_messaging_forbidden(request)
    if err:
        return err
    services.ensure_public_schema()
    sid = services.resolve_school_pk_for_user(request.user)
    if sid is None:
        return JsonResponse({"ok": False, "error": "No school assigned"}, status=403)
    body = _json_body(request)
    archived = body.get("archived")
    if archived is not None:
        archived = bool(archived)
    mark_unread = bool(body.get("mark_unread"))
    services.set_thread_state(
        sid,
        viewer_is_superadmin=False,
        pinned=None,
        archived=archived,
        mark_unread=mark_unread,
    )
    return JsonResponse({"ok": True})
