from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import User


def role_required(*allowed_roles: str):
    """
    Restrict a view to users with specific roles.
    Always enforces authentication.
    """

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped_view(request, *args, **kwargs):
            user: User = request.user  # type: ignore[assignment]
            if getattr(user, "role", None) in allowed_roles:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied

        return _wrapped_view

    return decorator


superadmin_required = role_required(User.Roles.SUPERADMIN)
admin_required = role_required(User.Roles.ADMIN)
teacher_required = role_required(User.Roles.TEACHER)
student_required = role_required(User.Roles.STUDENT)
parent_required = role_required(User.Roles.PARENT)


def feature_required(feature_code: str):
    """
    Restrict a view to schools that have the given feature enabled.
    Use after @admin_required or @login_required. Returns 403 if feature not available.
    Superadmin is exempt (no school = bypass).
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if getattr(request.user, "role", None) == User.Roles.SUPERADMIN:
                return view_func(request, *args, **kwargs)
            features = getattr(request, "school_features", frozenset())
            if feature_code not in features:
                raise PermissionDenied(
                    f"Feature '{feature_code}' is not available in your plan."
                )
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator

