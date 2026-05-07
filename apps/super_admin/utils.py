from apps.accounts.models import User


def has_feature(user, feature_code: str) -> bool:
    """
    Unified feature gate for views and templates.

    - Superadmin: always allowed.
    - Others: feature must be enabled in user's school plan.
    """
    if not feature_code:
        return False
    if user is not None and getattr(user, "role", None) == User.Roles.SUPERADMIN:
        return True
    school = getattr(user, "school", None)
    if not school:
        return False
    try:
        return school.has_feature(feature_code)
    except Exception:
        return False

