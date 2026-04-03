"""
Decide when *not* to force the PostgreSQL schema to the logged-in user's school.

django-tenants' TenantMainMiddleware may set the schema from the Host header (e.g. tenant.localhost).
That must be overridden for authenticated school users on shared hosts so the connection always
matches request.user.school — otherwise School B's session on School A's domain leaks A's data.
"""
from __future__ import annotations

import re

# Public / platform routes: keep whatever TenantMainMiddleware chose (usually public).
TENANT_BIND_EXEMPT_PREFIXES = (
    "/static/",
    "/media/",
    "/accounts/",
    "/api/",
    "/admin/",
    "/django-admin/",
)

TENANT_BIND_EXEMPT_PATHS = frozenset({"/", "/favicon.ico"})

TENANT_BIND_EXEMPT_MARKETING_PREFIXES = (
    "/pricing/",
    "/about/",
    "/contact/",
    "/enroll/",
    "/superadmin/",
    "/super-admin/",
)

# Logged-in user may open another school's public portal; the view switches schema via school_code.
_SCHOOL_PUBLIC_PORTAL_RE = re.compile(
    r"^/school/[A-Za-z0-9_-]+/(admission|results)(/|$)"
)


def path_exempts_user_tenant_bind(path: str) -> bool:
    if not path:
        p = "/"
    else:
        p = path if path.startswith("/") else f"/{path}"
    if p in TENANT_BIND_EXEMPT_PATHS:
        return True
    for prefix in TENANT_BIND_EXEMPT_PREFIXES:
        if p.startswith(prefix):
            return True
    for prefix in TENANT_BIND_EXEMPT_MARKETING_PREFIXES:
        if p.startswith(prefix):
            return True
    if _SCHOOL_PUBLIC_PORTAL_RE.match(p):
        return True
    return False
