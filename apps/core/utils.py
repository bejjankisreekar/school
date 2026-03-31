"""
Utility helpers for core app.
"""
from datetime import date


def add_warning_once(request, session_key: str, message: str):
    """
    No-op: Bootstrap alert notifications have been removed from the project.
    Kept for API compatibility with existing callers.
    """
    pass


def has_feature_access(school, feature_code: str, *, user=None) -> bool:
    """
    DB-driven feature check for templates and views.

    - Platform superadmin always has access (owner / operations).
    - Otherwise uses `school.get_enabled_feature_codes()` (plan + per-school override).
    """
    if not feature_code:
        return False
    if user is not None and getattr(user, "role", None) == "SUPERADMIN":
        return True
    if not school:
        return False
    try:
        codes = school.get_enabled_feature_codes()
    except Exception:
        return False
    return feature_code in (codes or set())


def get_current_academic_year() -> str:
    """
    Return academic year label in `YYYY-YYYY` format.
    Academic year starts in June.
    """
    today = date.today()
    if today.month >= 6:
        return f"{today.year}-{today.year + 1}"
    return f"{today.year - 1}-{today.year}"


def get_current_academic_year_bounds() -> tuple[date, date]:
    """
    Return (start_date, end_date) for current academic year.
    Academic year window: June 1 to May 31.
    """
    today = date.today()
    if today.month >= 6:
        start_year = today.year
    else:
        start_year = today.year - 1
    start_date = date(start_year, 6, 1)
    end_date = date(start_year + 1, 5, 31)
    return start_date, end_date


def get_active_academic_year_obj():
    try:
        from apps.school_data.models import AcademicYear

        return AcademicYear.objects.filter(is_active=True).order_by("-start_date").first()
    except Exception:
        return None


def apply_active_year_filter(qs, field_name: str = "academic_year"):
    """Apply active academic-year filter to a queryset when possible."""
    ay = get_active_academic_year_obj()
    if not ay:
        return qs
    try:
        return qs.filter(**{f"{field_name}_id": ay.id})
    except Exception:
        return qs


def teacher_class_section_pairs_display(teacher):
    """
    Class + section pairs a teacher may use for homework, exams, attendance, etc.

    Combines ClassSectionSubjectTeacher with Teacher.classrooms × each class's
    linked sections. Admins often assign only "Assigned classes" on the teacher
    profile without creating CSST rows; those teachers were incorrectly blocked.
    """
    if not teacher:
        return []
    from apps.school_data.models import ClassSectionSubjectTeacher

    seen = set()
    out = []

    def _add(cn, sn):
        if cn is None or sn is None:
            return
        cn = str(cn).strip()
        sn = str(sn).strip()
        if not cn or not sn:
            return
        key = (cn.lower(), sn.lower())
        if key in seen:
            return
        seen.add(key)
        out.append((cn, sn))

    for cn, sn in (
        ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
        .values_list("class_obj__name", "section__name")
        .distinct()
    ):
        _add(cn, sn)

    for classroom in teacher.classrooms.all().prefetch_related("sections"):
        cn = classroom.name
        for sec in classroom.sections.all():
            _add(cn, sec.name)

    return out


def teacher_allowed_class_section_pairs_lower(teacher):
    """Set of (class_name.lower(), section_name.lower()) for permission checks."""
    return {(c.lower(), s.lower()) for c, s in teacher_class_section_pairs_display(teacher)}


def tenant_migrate_cli_hint(school=None) -> str:
    """
    Shell command to migrate the current tenant schema (django-tenants).

    Prefer School.schema_name (PostgreSQL schema), not School.code.
    Falls back to connection.schema_name, then a placeholder.
    """
    from django.db import connection

    if school is not None:
        sn = getattr(school, "schema_name", None)
        if sn:
            return f"python manage.py migrate_schemas -s {sn}"

    schema = getattr(connection, "schema_name", None)
    try:
        from django_tenants.utils import get_public_schema_name

        public = get_public_schema_name()
    except Exception:
        public = "public"
    if schema and schema != public:
        return f"python manage.py migrate_schemas -s {schema}"
    return (
        "python manage.py migrate_schemas -s <schema_name> "
        "(run python manage.py list_school_schemas to see names)"
    )
