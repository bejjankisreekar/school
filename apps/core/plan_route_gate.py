"""
Resolve which SaaS feature code (if any) guards the current URL for tenant users.

Uses public-schema ``SidebarMenuItem`` (route_name + feature_code) per role, plus
path-prefix fallbacks for detail URLs not listed as separate menu rows.
"""
from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django_tenants.utils import get_public_schema_name, schema_context

from apps.core.subscription_access import normalize_feature_key, plan_access_denied_json
from apps.core.tenant_bind import _SCHOOL_PUBLIC_PORTAL_RE

from apps.accounts.models import User

logger = logging.getLogger(__name__)

_CACHE_KEY_PREFIX = "plan_route_gate:route_features:v2:"
_CACHE_TTL = 120

# Longer prefix first (first match wins).
_PATH_PREFIX_FEATURES: tuple[tuple[str, str], ...] = (
    ("/school/admissions/", "online_admission"),
    ("/school/billing/", "fees"),
    ("/school/exams/", "exams"),
    ("/school/homework/", "homework"),
    ("/school/students/", "students"),
    ("/school/teachers/", "teachers"),
    ("/school/classes/", "classes"),
    ("/school/sections/", "sections"),
    ("/school/subjects/", "subjects"),
    ("/school/academic-years/", "academic_year"),
    ("/school/promote-students/", "students"),
    ("/school/staff-attendance/", "attendance"),
    ("/school/library/", "library"),
    ("/school/hostel/", "hostel"),
    ("/school/transport/", "transport"),
    ("/school/payroll/", "payroll"),
    ("/school/payslips/", "payroll"),
    ("/school/branding/", "custom_branding"),
    ("/school/reports/", "reports"),
    ("/school/timeslots/", "timetable"),
    ("/school/timetable/", "timetable"),
    ("/school/calendar/", "calendar"),
    ("/school/notifications/", "notifications"),
    ("/school/messages/", "platform_messaging"),
    ("/school-admin/messages/", "platform_messaging"),
    ("/attendance/", "attendance"),
    ("/marks/", "exams"),
    ("/homework/", "homework"),
    ("/teacher/exams/", "exams"),
    ("/teacher/homework/", "homework"),
    ("/teacher/attendance/", "attendance"),
    ("/teacher/marks/", "exams"),
    ("/teacher/class-analytics/", "reports"),
    ("/student/exam", "exams"),
    ("/student/homework/", "homework"),
    ("/student/attendance", "attendance"),
    ("/student/fees/", "fees"),
    ("/student/reports/", "reports"),
    ("/student/timetable/", "timetable"),
    ("/student/report-card", "exams"),
    ("/student/cumulative-report", "reports"),
    ("/student/attendance-report", "reports"),
    ("/student/messages/", "reports"),
    ("/teacher/messages/", "reports"),
    ("/api/master-data/", "students"),
    ("/api/master-dropdown/", "students"),
    ("/api/exams/create/", "exams"),
    ("/api/subjects/save-order/", "students"),
    ("/api/sections/", "students"),
    ("/api/students/", "students"),
    ("/school-admin/messages/api/", "platform_messaging"),
    ("/student/messages/api/", "reports"),
    ("/teacher/messages/api/students/", "reports"),
    ("/teacher/messages/api/", "reports"),
)

_EXEMPT_PATH_PREFIXES: tuple[str, ...] = (
    "/static/",
    "/media/",
    "/accounts/",
    "/admin/",
    "/django-admin/",
    "/super-admin/",
    "/superadmin/",
    "/pricing/",
    "/about/",
    "/contact/",
    "/enroll/",
)

_EXEMPT_PATHS = frozenset(
    {
        "/",
        "/favicon.ico",
    }
)

# Always reachable without a plan feature (dashboards, auth-adjacent, trial UX).
_ALWAYS_ALLOW_ROUTE_KEYS: frozenset[str] = frozenset(
    {
        "core:admin_dashboard",
        "core:teacher_dashboard",
        "core:student_dashboard",
        "core:parent_dashboard",
        "accounts:login",
        "accounts:logout",
        "accounts:portal_login",
        "accounts:access_restricted",
        "accounts:account_profile",
        "accounts:account_settings",
        "accounts:password_change",
        "accounts:password_reset",
        "accounts:password_reset_done",
        "accounts:password_reset_confirm",
        "core:student_profile_settings",
        "core:edit_profile",
        "core:edit_profile_web",
    }
)


def _resolver_route_key(resolver_match) -> str:
    if not resolver_match:
        return ""
    ns = (getattr(resolver_match, "namespace", None) or "").strip()
    un = (getattr(resolver_match, "url_name", None) or "").strip()
    if ns and un:
        return f"{ns}:{un}"
    return un


def _sidebar_route_feature_map(role: str) -> dict[str, str]:
    cache_key = f"{_CACHE_KEY_PREFIX}{role}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    out: dict[str, str] = {}
    try:
        from apps.core.models import SidebarMenuItem

        with schema_context(get_public_schema_name()):
            qs = (
                SidebarMenuItem.objects.filter(
                    role=role,
                    is_active=True,
                    is_visible=True,
                )
                .exclude(route_name="")
                .exclude(feature_code="")
                .values_list("route_name", "feature_code")
            )
            for rn, fc in qs:
                rn = (rn or "").strip()
                fc = (fc or "").strip()
                if rn and fc:
                    out[rn] = fc
    except Exception:
        logger.exception("plan_route_gate: could not load SidebarMenuItem for role=%s", role)
    cache.set(cache_key, out, _CACHE_TTL)
    return out


def _prefix_feature(path: str) -> str | None:
    p = (path or "").split("?", 1)[0]
    if not p.startswith("/"):
        p = f"/{p}"
    for prefix, code in sorted(_PATH_PREFIX_FEATURES, key=lambda x: -len(x[0])):
        if p.startswith(prefix):
            return code
    return None


def required_feature_for_request(request, resolver_match) -> str | None:
    """
    Return a feature code if this request should be gated, else None.
    """
    if not resolver_match:
        return None
    path = request.path or ""
    if path in _EXEMPT_PATHS:
        return None
    for prefix in _EXEMPT_PATH_PREFIXES:
        if path.startswith(prefix):
            return None
    path_only = path.split("?", 1)[0] or "/"
    if _SCHOOL_PUBLIC_PORTAL_RE.match(path_only):
        return None

    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "role", None) == User.Roles.SUPERADMIN:
        return None
    role = getattr(user, "role", None)
    if role not in (
        User.Roles.ADMIN,
        User.Roles.TEACHER,
        User.Roles.STUDENT,
        User.Roles.PARENT,
    ):
        return None
    if not getattr(user, "school", None):
        return None

    route_key = _resolver_route_key(resolver_match)
    if route_key in _ALWAYS_ALLOW_ROUTE_KEYS:
        return None

    role_map = _sidebar_route_feature_map(role)
    req = role_map.get(route_key)
    if req:
        return normalize_feature_key(req)
    pf = _prefix_feature(path)
    return normalize_feature_key(pf) if pf else None


def feature_label(feature_code: str) -> str:
    code = (feature_code or "").strip()
    if not code:
        return "This module"
    return code.replace("_", " ").strip().title()


def plan_feature_denied_response(request, feature_code: str) -> HttpResponse:
    """HTML page for school admins; JSON for API-style requests; plain 403 for others."""
    accept = (request.META.get("HTTP_ACCEPT") or "").lower()
    wants_json = (
        ("application/json" in accept and "text/html" not in accept)
        or (request.headers.get("X-Requested-With") == "XMLHttpRequest" and "text/html" not in accept)
        or ((getattr(request, "content_type", None) or "").startswith("application/json"))
    )
    if wants_json:
        return plan_access_denied_json()

    role = getattr(getattr(request, "user", None), "role", None)
    ctx: dict[str, Any] = {
        "feature_code": feature_code,
        "feature_label": feature_label(feature_code),
        "is_school_admin": role == User.Roles.ADMIN,
        "is_teacher": role == User.Roles.TEACHER,
        "is_student": role == User.Roles.STUDENT,
        "is_parent": role == User.Roles.PARENT,
    }
    try:
        if role == User.Roles.ADMIN:
            ctx["dashboard_url"] = reverse("core:admin_dashboard")
        elif role == User.Roles.TEACHER:
            ctx["dashboard_url"] = reverse("core:teacher_dashboard")
        elif role == User.Roles.STUDENT:
            ctx["dashboard_url"] = reverse("core:student_dashboard")
        elif role == User.Roles.PARENT:
            ctx["dashboard_url"] = reverse("core:parent_dashboard")
        else:
            ctx["dashboard_url"] = reverse("core:home")
    except NoReverseMatch:
        ctx["dashboard_url"] = "/"

    return render(
        request,
        "core/plan_feature_denied.html",
        ctx,
        status=403,
    )


def clear_route_feature_cache() -> None:
    """Call after bulk SidebarMenuItem edits if you need immediate effect without waiting for TTL."""
    for role in ("ADMIN", "TEACHER", "STUDENT", "PARENT", "SUPERADMIN"):
        cache.delete(f"{_CACHE_KEY_PREFIX}{role}")
