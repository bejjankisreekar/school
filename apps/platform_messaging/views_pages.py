from __future__ import annotations

import json

from django.db import connection
from django.shortcuts import render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET

from apps.accounts.decorators import feature_required, role_required, superadmin_required
from apps.accounts.models import User

from .access import FEATURE_CODE


@superadmin_required
@require_GET
def superadmin_messages_page(request):
    connection.set_schema_to_public()
    _sid_placeholder = 999_999_999
    cfg = {
        "role": "superadmin",
        "threadsUrl": reverse("core:super_admin:platform_messages_api_threads"),
        "messagesUrlTemplate": reverse(
            "core:super_admin:platform_messages_api_messages", kwargs={"school_id": _sid_placeholder}
        ).replace(str(_sid_placeholder), "__SCHOOL_ID__"),
        "sendUrl": reverse("core:super_admin:platform_messages_api_send"),
        "markReadUrl": reverse("core:super_admin:platform_messages_api_mark_read"),
        "threadStateUrl": reverse("core:super_admin:platform_messages_api_thread_state"),
        "broadcastUrl": reverse("core:super_admin:platform_messages_api_broadcast"),
    }
    return render(
        request,
        "platform_messaging/superadmin_inbox.html",
        {
            "page_title": "Messages",
            "inbox_config_json": mark_safe(json.dumps(cfg)),
            "show_messaging_back_nav": False,
            "messaging_search_placeholder": "Search by school or message…",
        },
    )


@role_required(User.Roles.ADMIN)
@feature_required(FEATURE_CODE)
@require_GET
def school_admin_messages_page(request):
    from . import services

    user = request.user
    if not getattr(user, "school", None):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied

    services.ensure_public_schema()
    sid = services.resolve_school_pk_for_user(user)
    if sid is None:
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied
    cfg = {
        "role": "schooladmin",
        "schoolId": sid,
        "threadsUrl": reverse("core:school_admin_platform_messages_api_threads"),
        "messagesUrl": reverse("core:school_admin_platform_messages_api_messages", kwargs={"school_id": sid}),
        "sendUrl": reverse("core:school_admin_platform_messages_api_send"),
        "markReadUrl": reverse("core:school_admin_platform_messages_api_mark_read"),
        "threadStateUrl": reverse("core:school_admin_platform_messages_api_thread_state"),
        "broadcastUrl": "",
    }

    return render(
        request,
        "platform_messaging/inbox.html",
        {
            "page_title": "Messages to platform",
            "inbox_config_json": mark_safe(json.dumps(cfg)),
            "show_messaging_back_nav": True,
            "messaging_search_placeholder": "Search in this thread…",
        },
    )
