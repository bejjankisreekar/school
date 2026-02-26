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


admin_required = role_required(User.Roles.ADMIN)
teacher_required = role_required(User.Roles.TEACHER)
student_required = role_required(User.Roles.STUDENT)

