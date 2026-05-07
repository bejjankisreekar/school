"""Plan / feature gates for platform messaging (public schema)."""

from apps.accounts.models import User
from apps.core.subscription_access import has_feature_access

FEATURE_CODE = "platform_messaging"


def school_admin_can_use_platform_messaging(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "role", None) != User.Roles.ADMIN:
        return False
    school = getattr(user, "school", None)
    if not school:
        return False
    return has_feature_access(school.pk, FEATURE_CODE)
