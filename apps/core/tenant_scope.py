"""
Re-bind the PostgreSQL connection to the signed-in user's school tenant.

django-tenants uses search_path ``<tenant>, public`` so shared tables (e.g. accounts_user)
resolve from public while tenant apps (school_data, timetable, payroll) resolve from the
school schema first. Setting search_path to tenant-only would break auth and School lookups.

Call ``ensure_tenant_for_request`` from views if you suspect the connection was reset mid-request.
Middleware also runs this at process_view time for every authenticated school user.
"""
from __future__ import annotations

from django.db import connection

from apps.core.tenant_bind import path_exempts_user_tenant_bind


def ensure_tenant_for_request(request) -> None:
    """
    If the user has a school, force ``connection.set_tenant(school)`` unless the path is exempt.
    Idempotent; safe to call multiple times per request.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return
    school = getattr(user, "school", None)
    if not school:
        return
    path = request.path or "/"
    if path_exempts_user_tenant_bind(path):
        return
    connection.set_tenant(school)
