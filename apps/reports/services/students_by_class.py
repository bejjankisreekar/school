"""
Students by class report — aggregates from ClassRoom + Student (optional AcademicYear filter).
"""
from __future__ import annotations

from django.db.models import Count, Q

from apps.school_data.models import AcademicYear, ClassRoom, Student


def get_students_by_class_data(school, academic_year_id: int | None) -> dict:
    """
    Returns class_rows, chart_labels, chart_counts for the given school.
    If academic_year_id is None, all enrolled students (any academic year) are counted.
    If set, only students with that academic_year_id are included.
    """
    if academic_year_id is not None:
        student_q = Q(students__user__school=school) & Q(students__academic_year_id=academic_year_id)
        enrolled = Student.objects.filter(user__school=school, academic_year_id=academic_year_id)
    else:
        student_q = Q(students__user__school=school)
        enrolled = Student.objects.filter(user__school=school)

    class_rows = list(
        ClassRoom.objects.annotate(total=Count("students", filter=student_q))
        .values("id", "name", "total")
        .order_by("name")
    )
    for row in class_rows:
        row["total"] = int(row["total"] or 0)

    unassigned = enrolled.filter(classroom__isnull=True).count()
    if unassigned:
        class_rows.append({"id": None, "name": "Unassigned", "total": unassigned})

    chart_labels = [r["name"] or "—" for r in class_rows]
    chart_counts = [r["total"] for r in class_rows]

    return {
        "class_rows": class_rows,
        "chart_labels": chart_labels,
        "chart_counts": chart_counts,
    }


def get_default_academic_year_id(school) -> int | None:
    """Prefer active academic year; else None (all years)."""
    active = AcademicYear.objects.filter(is_active=True).values_list("pk", flat=True).first()
    return int(active) if active else None


def parse_academic_year_param(raw: str | None, school) -> int | None:
    """
    'all' or empty → None (all years).
    Integer pk → filter by that year if it exists.
    """
    if raw is None or raw == "" or str(raw).lower() == "all":
        return None
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return get_default_academic_year_id(school)
    if not AcademicYear.objects.filter(pk=pk).exists():
        return get_default_academic_year_id(school)
    return pk
