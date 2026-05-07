from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse
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
            # UX: if a logged-in user hits a route for another role,
            # send them to their own dashboard instead of a hard 403 page.
            role = getattr(user, "role", None)
            try:
                if role == User.Roles.STUDENT:
                    return redirect("core:student_dashboard")
                if role == User.Roles.TEACHER:
                    return redirect("core:teacher_dashboard")
                if role == User.Roles.PARENT:
                    return redirect("core:parent_dashboard")
            except Exception:
                # Fall through to strict 403 if routes aren't available.
                pass
            raise PermissionDenied

        return _wrapped_view

    return decorator


superadmin_required = role_required(User.Roles.SUPERADMIN)
# School admin UI; platform superadmin may open the same routes (full visibility, no plan gate).
admin_required = role_required(User.Roles.ADMIN, User.Roles.SUPERADMIN)
teacher_required = role_required(User.Roles.TEACHER)
teacher_or_admin_required = role_required(User.Roles.TEACHER, User.Roles.ADMIN, User.Roles.SUPERADMIN)
student_required = role_required(User.Roles.STUDENT)
parent_required = role_required(User.Roles.PARENT)


def feature_required(feature_code: str):
    """
    Restrict a view to schools that have the given feature enabled.
    Use after @admin_required or @login_required. Returns a plan-denied page (or JSON 403) if not available.
    Superadmin is exempt (no school = bypass).
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if getattr(request.user, "role", None) == User.Roles.SUPERADMIN:
                return view_func(request, *args, **kwargs)
            features = getattr(request, "school_features", frozenset())
            from apps.core.plan_features import feature_granted

            if not feature_granted(features, feature_code):
                from apps.core.plan_route_gate import plan_feature_denied_response

                return plan_feature_denied_response(request, feature_code)
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator


api_plan_feature_required = feature_required
"""Alias for JSON/API views; identical to ``feature_required`` (plan denial returns JSON when appropriate)."""

