"""
Re-bind the PostgreSQL connection to the signed-in user's school tenant.

django-tenants uses search_path ``<tenant>, public`` so shared tables (e.g. accounts_user)
resolve from public while tenant apps (school_data, timetable, payroll) resolve from the
school schema first. Setting search_path to tenant-only would break auth and School lookups.

Call ``ensure_tenant_for_request`` from views if you suspect the connection was reset mid-request.
Middleware also runs this at process_view time for every authenticated school user.
"""
from __future__ import annotations

import logging

from django.db import connection, connections

from apps.core.tenant_bind import path_exempts_user_tenant_bind

logger = logging.getLogger(__name__)


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
    # Bind tenant on both the default connection and the tenant DB alias connection.
    # In this project some recovery paths operate on the tenant alias directly; if only
    # the default connection is bound, ORM queries may hit the wrong search_path and
    # appear to "return empty" even when tenant data exists.
    try:
        connection.set_tenant(school)
    except Exception:
        pass
    try:
        from django_tenants.utils import get_tenant_database_alias

        alias = get_tenant_database_alias()
        if alias in connections:
            connections[alias].set_tenant(school)
    except Exception:
        pass

    logger.debug(
        "ensure_tenant_for_request user_id=%s school_schema=%s conn_schema=%s",
        getattr(user, "id", None),
        getattr(school, "schema_name", None),
        getattr(connection, "schema_name", None),
    )
    from apps.core.tenant_schema_repair import ensure_core_school_data_tables_if_needed

    ensure_core_school_data_tables_if_needed(request, school)
