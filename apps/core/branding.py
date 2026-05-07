"""Resolved platform / product name for templates, emails, and UI copy."""

from __future__ import annotations

from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError


def get_platform_product_name() -> str:
    """
    Name shown across the product (sidebar, marketing, login, titles).

    Uses Control Center ``platform_name`` when readable; otherwise
    ``settings.APP_PRODUCT_NAME`` (default 'Campus ERP').
    """
    name = getattr(settings, "APP_PRODUCT_NAME", None) or "Campus ERP"
    try:
        from apps.super_admin.models import ControlCenterSettings

        solo = ControlCenterSettings.get_solo()
        pn = (getattr(solo, "platform_name", None) or "").strip()
        if pn:
            name = pn
    except (OperationalError, ProgrammingError, ImportError):
        pass
    return name
