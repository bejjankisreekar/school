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
from django.contrib import messages
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
    from apps.core.subscription_access import get_cached_enabled_feature_codes, normalize_feature_key

    return frozenset(
        normalize_feature_key(str(c)) for c in get_cached_enabled_feature_codes(school)
    )


def _superadmin_feature_union() -> frozenset:
    """All known feature codes from DB plus static list (for new codes before cache)."""
    from apps.core.plan_features import PREMIUM_FEATURES, materialize_feature_set
    from apps.core.subscription_access import normalize_feature_key

    try:
        from apps.customers.models import Feature

        db_codes = {normalize_feature_key(str(c)) for c in Feature.objects.values_list("code", flat=True)}
    except Exception:
        db_codes = set()
    try:
        from apps.super_admin.models import Feature as SaFeature

        sa_codes = {normalize_feature_key(str(c)) for c in SaFeature.objects.values_list("code", flat=True)}
    except Exception:
        sa_codes = set()
    merged = set(_SUPERADMIN_ALL_FEATURES) | set(PREMIUM_FEATURES) | set(db_codes) | set(sa_codes)
    return materialize_feature_set(merged)


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


class PlanRouteGateMiddleware(MiddlewareMixin):
    """
    Enforce SaaS plan features for school admin, teacher, student, and parent routes.

    Uses ``SidebarMenuItem`` route_name → feature_code (public schema) plus path-prefix
    fallbacks for detail URLs. Super Admin and unauthenticated requests are not gated here.
    """

    def process_view(self, request, view_func, view_args, view_kwargs):
        from apps.core.plan_route_gate import plan_feature_denied_response, required_feature_for_request

        rm = getattr(request, "resolver_match", None)
        if not rm:
            return None
        req_feature = required_feature_for_request(request, rm)
        if not req_feature:
            return None
        from apps.core.plan_features import feature_granted

        features = getattr(request, "school_features", frozenset())
        if feature_granted(features, req_feature):
            return None
        return plan_feature_denied_response(request, req_feature)


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


class PlatformLoginLockMiddleware(MiddlewareMixin):
    """
    If platform_control_meta.disable_login is true for the user's school, sign them out
    and send them to login (superadmin exempt).
    """

    def process_request(self, request):
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return
        if getattr(request.user, "role", None) == User.Roles.SUPERADMIN:
            return
        school = getattr(request.user, "school", None)
        if not school:
            return
        path = request.path or ""
        if path.startswith("/accounts/"):
            return
        if path.startswith("/static/") or path.startswith("/media/"):
            return
        meta = getattr(school, "platform_control_meta", None) or {}
        if not isinstance(meta, dict) or not meta.get("disable_login"):
            return
        from django.contrib.auth import logout

        logout(request)
        messages.error(
            request,
            "Your school login has been disabled by the platform administrator. Contact support.",
        )
        return redirect(reverse("accounts:login"))


class SuspendedSchoolMiddleware(MiddlewareMixin):
    """
    Block tenant users when the school is archived, suspended, or fully inactive (status enum).
    Soft inactivate (is_active False while trial/active) still allows the session; see
    SchoolSoftInactiveNoticeMiddleware for the in-app notice.
    Superadmin is exempt.
    """

    def process_request(self, request):
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return
        if getattr(request.user, "role", None) == User.Roles.SUPERADMIN:
            return
        school = getattr(request.user, "school", None)
        if not school:
            return
        path = request.path or ""
        if path.startswith("/accounts/"):
            return
        if path.startswith("/static/") or path.startswith("/media/"):
            return
        if school.allows_tenant_user_login():
            return
        from django.contrib.auth import logout

        logout(request)
        messages.error(
            request,
            "This school account is archived, suspended, or inactive on the platform. Contact support if you need access restored.",
        )
        return redirect(reverse("accounts:login"))


class SchoolSoftInactiveNoticeMiddleware(MiddlewareMixin):
    """
    Once per session, show a warning when the school is soft-inactivated (is_active False)
    but login is still allowed (trial/active status, not suspended/archived).
    """

    _SESSION_FLAG = "school_soft_inactive_notice_shown"

    def process_request(self, request):
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return
        if getattr(request.user, "role", None) == User.Roles.SUPERADMIN:
            return
        school = getattr(request.user, "school", None)
        if not school or school.is_active:
            return
        if not school.allows_tenant_user_login():
            return
        if request.session.get(self._SESSION_FLAG):
            return
        path = request.path or ""
        if path.startswith("/accounts/") or path.startswith("/static/") or path.startswith("/media/"):
            return
        messages.warning(
            request,
            "Your school account is marked inactive on the platform. Some actions may be limited until the School Admin or platform support reactivates it.",
        )
        request.session[self._SESSION_FLAG] = True
        return None


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
        # Always allow auth/account endpoints so users can logout or switch accounts.
        path = request.path or ""
        if path.startswith("/accounts/"):
            return
        # Allow company contact page even during expiry (so "Contact Admin" works).
        if path.startswith("/contact/"):
            return
        if path.startswith("/static/") or path.startswith("/media/"):
            return

        role = getattr(request.user, "role", None)
        # For portal users (student/teacher/parent), do not show pricing page.
        # Instead, block access and ask them to contact school admin.
        if role in (User.Roles.STUDENT, User.Roles.TEACHER, User.Roles.PARENT):
            from django.contrib.auth import logout

            try:
                logout(request)
            except Exception:
                pass
            try:
                messages.error(request, "School plan has expired. Please contact the School Admin.")
            except Exception:
                pass
            try:
                return redirect(
                    f"{reverse('accounts:access_restricted')}?type=trial_expired&role={role}&login_type=portal"
                )
            except Exception:
                return redirect("accounts:portal_login")

        # Only school admin should see the trial-expired plans screen.
        # Allow access to admin dashboard (it will render trial_expired)
        try:
            dashboard_url = reverse("core:admin_dashboard")
            if request.path == dashboard_url or request.path.rstrip("/") == dashboard_url.rstrip("/"):
                return
        except Exception:
            pass
        return redirect("core:admin_dashboard")


class DbConnectionSanitizeMiddleware(MiddlewareMixin):
    """
    After each response, drop idle or broken PostgreSQL connections safely.

    Runs after the request transaction (ATOMIC_REQUESTS) has finished so we do not
    invalidate in-flight named cursors mid-view. Helps avoid stale handles when
    CONN_MAX_AGE keeps connections open across tenant/schema changes.
    """

    def process_response(self, request, response):
        try:
            from django.db import connections

            try:
                from django_tenants.utils import get_tenant_database_alias

                alias = get_tenant_database_alias()
            except Exception:
                alias = "default"
            conn = connections[alias]
            if conn.connection is not None:
                conn.close_if_unusable_or_obsolete()
        except Exception:
            pass
        return response
