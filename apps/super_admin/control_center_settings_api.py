"""
Control Center settings — GET / POST APIs (singleton row).
"""
from __future__ import annotations

import json

from django.db import connection, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import superadmin_required

from .models import ControlCenterSettings
from .settings_service import apply_payload, reset_to_defaults, serialize_for_api


def _logo_url(obj: ControlCenterSettings) -> str:
    try:
        if obj.logo and getattr(obj.logo, "name", ""):
            return obj.logo.url
    except Exception:
        pass
    return ""


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def control_center_settings_get_api(request):
    connection.set_schema_to_public()
    obj = ControlCenterSettings.get_solo()
    data = serialize_for_api(obj)
    data["logo_url"] = _logo_url(obj)
    return JsonResponse({"ok": True, "settings": data})


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def control_center_settings_update_api(request):
    connection.set_schema_to_public()
    if request.content_type.startswith("multipart/form-data"):
        obj = ControlCenterSettings.get_solo()
        section = (request.POST.get("section") or "").strip()
        if section != "platform":
            return JsonResponse({"ok": False, "error": "Multipart uploads are only supported for platform (logo)."}, status=400)
        data = {
            "platform_name": (request.POST.get("platform_name") or "").strip(),
            "default_language": (request.POST.get("default_language") or "").strip(),
            "timezone": (request.POST.get("timezone") or "").strip(),
            "default_theme": (request.POST.get("default_theme") or "").strip(),
        }
        err = apply_payload(obj, "platform", data)
        if err:
            return JsonResponse({"ok": False, "error": err}, status=400)
        if request.FILES.get("logo"):
            obj.logo = request.FILES["logo"]
            obj.save(update_fields=["logo", "updated_at"])
        out = serialize_for_api(ControlCenterSettings.objects.get(pk=1))
        out["logo_url"] = _logo_url(obj)
        return JsonResponse({"ok": True, "settings": out})

    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"ok": False, "error": "Body must be an object"}, status=400)
    section = (body.get("section") or "").strip()
    if not section:
        return JsonResponse({"ok": False, "error": "Missing section"}, status=400)
    payload = {k: v for k, v in body.items() if k != "section"}
    obj = ControlCenterSettings.get_solo()
    err = apply_payload(obj, section, payload)
    if err:
        return JsonResponse({"ok": False, "error": err}, status=400)
    obj.refresh_from_db()
    out = serialize_for_api(obj)
    out["logo_url"] = _logo_url(obj)
    return JsonResponse({"ok": True, "settings": out})


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def control_center_settings_reset_api(request):
    connection.set_schema_to_public()
    obj = ControlCenterSettings.get_solo()
    reset_to_defaults(obj)
    obj = ControlCenterSettings.objects.get(pk=1)
    out = serialize_for_api(obj)
    out["logo_url"] = _logo_url(obj)
    return JsonResponse({"ok": True, "settings": out})
