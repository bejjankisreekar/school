"""
Ensures tenant schema is set when authenticated users with a school access localhost.
Student/teacher/admin/parent views use school_data and timetable (tenant apps).
When on public schema (localhost) with no subdomain, switch to user's school schema.

Trial expiry: redirects school users (non-superadmin) to dashboard when trial expired.

Feature middleware: loads school_features onto request for feature-based access control.
"""
from django.utils.deprecation import MiddlewareMixin
from django.shortcuts import redirect
from django.urls import reverse
from django_tenants.utils import get_public_schema_name

from apps.accounts.models import User

# Sidebar / feature_required: superadmin sees every module without a school plan.
_SUPERADMIN_ALL_FEATURES = frozenset(
    {
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


TENANT_PATHS = (
    "/student/",
    "/teacher/",
    "/school/",
    "/parent/",
    "/attendance/",
    "/marks/",
    "/homework/",
    "/reports/",
    "/students/",
    "/teachers/",
)


class TenantSchemaFromUserMiddleware(MiddlewareMixin):
    """
    When on public schema and user has a school, switch to that school's schema
    for tenant-dependent paths. This allows localhost:8000 to work for students/admins.
    """
    def process_request(self, request):
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return
        user = request.user
        school = getattr(user, "school", None)
        if not school:
            return
        from django.db import connection
        if connection.schema_name != get_public_schema_name():
            return  # Already on a tenant schema
        path = request.path
        if path.startswith("/admin/"):
            # Super admin paths - use public schema; views use tenant_context when needed
            return
        for prefix in TENANT_PATHS:
            if path.startswith(prefix):
                connection.set_tenant(school)
                break


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
