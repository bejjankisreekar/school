"""Unread counts for Super Admin ↔ School Admin platform messaging."""

from django.db import connection

from apps.accounts.models import User

from .access import school_admin_can_use_platform_messaging


def platform_messaging_badge(request):
    data = {"platform_messaging_unread": 0}
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return data
    role = getattr(user, "role", None)
    try:
        connection.set_schema_to_public()
        from apps.platform_messaging import services

        if role == User.Roles.SUPERADMIN:
            data["platform_messaging_unread"] = services.unread_count_for_superadmin()
        elif role == User.Roles.ADMIN and school_admin_can_use_platform_messaging(user):
            pk = services.resolve_school_pk_for_user(user)
            if pk is not None:
                data["platform_messaging_unread"] = services.unread_count_for_school_admin(pk)
    except Exception:
        return data
    return data
