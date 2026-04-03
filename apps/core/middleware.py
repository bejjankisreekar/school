"""
Force the DB schema to the signed-in user's school for almost all routes.

TenantMainMiddleware sets the schema from the Host header. On a shared host (localhost) or when
a user is logged in as School B but the hostname resolves to School A, queries would otherwise run
in the wrong schema (critical data leak). TenantSchemaFromUserMiddleware overrides that by calling
connection.set_tenant(user.school) after authentication, except for explicit exempt paths (accounts,
API, marketing, superadmin, public admission/results portals).

Trial expiry: redirects school users (non-superadmin) to dashboard when trial expired.

Feature middleware: loads school_features onto request for feature-based access control.
"""
from django.utils.deprecation import MiddlewareMixin
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.models import User
from apps.core.tenant_bind import path_exempts_user_tenant_bind
from apps.core.tenant_scope import ensure_tenant_for_request

# Sidebar / feature_required: superadmin sees every module without a school plan.
_SUPERADMIN_ALL_FEATURES = frozenset(
    {
        "students",
        "teachers",
        "attendance",
        "exams",
        "timetable",
        "fees",
        "payroll",
        "reports",
        "homework",
        "sms",
        "inventory",
        "ai_reports",
        "online_admission",
        "topper_list",
        "library",
        "hostel",
        "transport",
        "custom_branding",
    }
)


def _get_school_features(request):
    """Return set of feature codes for the user's school, or empty set if none."""
    school = getattr(getattr(request, "user", None), "school", None)
    if not school:
        return frozenset()
    # Strict SaaS-only access: rely on DB-driven feature codes.
    return frozenset(school.get_enabled_feature_codes())


def _superadmin_feature_union() -> frozenset:
    """All known feature codes from DB plus static list (for new codes before cache)."""
    try:
        from apps.customers.models import Feature

        db_codes = set(Feature.objects.values_list("code", flat=True))
    except Exception:
        db_codes = set()
    return frozenset(_SUPERADMIN_ALL_FEATURES | db_codes)


class SchoolFeaturesMiddleware(MiddlewareMixin):
    """
    Attach request.school_features (frozenset of feature codes) for the current user's school.
    Superadmin gets the full union so navigation and feature_required never hide modules.
    """

    def process_request(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and getattr(user, "role", None) == User.Roles.SUPERADMIN:
            request.school_features = _superadmin_feature_union()
        else:
            request.school_features = _get_school_features(request)


class TenantSchemaFromUserMiddleware(MiddlewareMixin):
    """
    Bind connection to request.user.school for every authenticated user with a school,
    unless the path is exempt (see apps.core.tenant_bind).
    """
    def process_request(self, request):
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return
        user = request.user
        school = getattr(user, "school", None)
        if not school:
            return
        path = request.path or "/"
        if path_exempts_user_tenant_bind(path):
            return
        from django.db import connection

        connection.set_tenant(school)

    def process_view(self, request, view_func, view_args, view_kwargs):
        """
        Re-apply tenant binding immediately before the view runs. Some middleware or
        early DB access can leave the connection on public or another host-resolved tenant;
        this prevents cross-school reads of school_data_* rows (e.g. classes list).
        """
        ensure_tenant_for_request(request)
        return None


class TenantSchemaFinalEnsureMiddleware(MiddlewareMixin):
    """
    Last process_request hook: re-bind connection to the user's school after Session,
    Messages, and other middleware. Defense-in-depth against stray public/host schema.
    Academic ORM (Subject, Section, ClassRoom) must see only the active tenant schema first.
    """

    def process_request(self, request):
        ensure_tenant_for_request(request)


class TrialExpiryMiddleware(MiddlewareMixin):
    """
    When a school user's trial has expired, redirect to admin dashboard.
    Admin dashboard renders trial_expired template for school admins.
    Superadmin is never blocked.
    """
    def process_request(self, request):
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return
        if getattr(request.user, "role", None) == User.Roles.SUPERADMIN:
            return
        school = getattr(request.user, "school", None)
        if not school or not school.is_trial_expired():
            return
        # Allow access to admin dashboard (it will render trial_expired)
        try:
            dashboard_url = reverse("core:admin_dashboard")
            if request.path == dashboard_url or request.path.rstrip("/") == dashboard_url.rstrip("/"):
                return
        except Exception:
            pass
        return redirect("core:admin_dashboard")
