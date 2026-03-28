"""
Summary metrics for the School Analytics Dashboard (reports hub).
"""
from __future__ import annotations

from django.db.utils import DatabaseError, InternalError, OperationalError, ProgrammingError
from django.utils import timezone

from apps.core.utils import has_feature_access
from apps.school_data.models import Attendance, ClassRoom, Student, Teacher


def _trend_new_this_month(
    count: int,
    *,
    zero_label: str = "No new this month",
    noun: str | None = None,
) -> tuple[str, bool]:
    """Returns (trend text, is_positive_highlight). Optional `noun` e.g. \"students\" → \"+3 new students this month\"."""
    if count > 0:
        if noun:
            return (f"+{count} new {noun} this month", True)
        return (f"+{count} new this month", True)
    return (zero_label, False)


def build_analytics_summary_metrics(school) -> dict:
    """
    Four top KPIs: students, teachers, classes, attendance today.
    Each includes value display string, trend line, icon, theme.
    """
    empty = {
        "analytics_metrics": [],
        "attendance_enabled": False,
    }
    if not school:
        return empty

    today = timezone.localdate()
    month_start = today.replace(day=1)

    total_students = Student.objects.filter(user__school=school).count()
    students_month = Student.objects.filter(
        user__school=school, created_on__date__gte=month_start
    ).count()
    st_trend, st_pos = _trend_new_this_month(students_month, noun="students")

    total_teachers = Teacher.objects.filter(user__school=school).count()
    teachers_month = Teacher.objects.filter(
        user__school=school, created_on__date__gte=month_start
    ).count()
    te_trend, te_pos = _trend_new_this_month(teachers_month, noun="teachers")

    total_classes = ClassRoom.objects.count()
    classes_month = ClassRoom.objects.filter(created_on__date__gte=month_start).count()
    cl_trend, cl_pos = _trend_new_this_month(
        classes_month, noun="classes", zero_label="No new classes this month"
    )

    attendance_on = has_feature_access(school, "attendance")
    att_value = "—"
    att_trend = "Enable attendance in your plan to track daily presence."
    att_pos = False
    present_count = 0

    if attendance_on:
        try:
            present_count = Attendance.objects.filter(
                date=today,
                status=Attendance.Status.PRESENT,
                student__user__school=school,
            ).count()
            if total_students:
                pct = round((present_count / total_students) * 100, 1)
                att_value = f"{pct}%"
                att_trend = f"{present_count} of {total_students} students present"
                att_pos = pct >= 50 or present_count > 0
            else:
                att_value = "0%"
                att_trend = "No enrolled students"
                att_pos = False
        except (DatabaseError, InternalError, OperationalError, ProgrammingError):
            att_value = "—"
            att_trend = "Could not load attendance data."
            att_pos = False

    metrics = [
        {
            "label": "Total Students",
            "value": str(total_students),
            "trend": st_trend,
            "trend_positive": st_pos,
            "icon": "bi-people-fill",
            "theme": "primary",
        },
        {
            "label": "Total Teachers",
            "value": str(total_teachers),
            "trend": te_trend,
            "trend_positive": te_pos,
            "icon": "bi-person-badge-fill",
            "theme": "info",
        },
        {
            "label": "Classes Running",
            "value": str(total_classes),
            "trend": cl_trend,
            "trend_positive": cl_pos,
            "icon": "bi-grid-3x3-gap-fill",
            "theme": "success",
        },
        {
            "label": "Attendance Today",
            "value": att_value,
            "trend": att_trend,
            "trend_positive": att_pos,
            "icon": "bi-calendar-check",
            "theme": "warning",
            "muted": not attendance_on,
        },
    ]

    return {
        "analytics_metrics": metrics,
        "attendance_enabled": attendance_on,
    }
