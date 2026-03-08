"""
Ensures tenant schema is set when authenticated users with a school access localhost.
Student/teacher/admin/parent views use school_data and timetable (tenant apps).
When on public schema (localhost) with no subdomain, switch to user's school schema.
"""
from django.utils.deprecation import MiddlewareMixin
from django_tenants.utils import get_public_schema_name


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
