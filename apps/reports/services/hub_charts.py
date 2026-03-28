"""
Preview chart data for the main /school/reports/ dashboard (hub).
"""
from __future__ import annotations

from datetime import timedelta

from django.db import connection
from django.db.utils import DatabaseError, InternalError, ProgrammingError
from django.utils import timezone

from apps.core.utils import has_feature_access
from apps.school_data.models import Attendance, Student

from .students_by_class import get_students_by_class_data


def build_hub_chart_context(school) -> dict:
    """
    Students-by-class (all years) + last-7-days attendance % for hub charts.
    """
    empty = {
        "hub_class_labels": [],
        "hub_class_counts": [],
        "hub_attendance_short_labels": [],
        "hub_attendance_full_labels": [],
        "hub_attendance_pcts": [],
        "hub_attendance_present": [],
        "hub_show_attendance_chart": False,
        "hub_attendance_unavailable_message": None,
    }
    if not school or not has_feature_access(school, "reports"):
        return empty

    payload = get_students_by_class_data(school, None)
    empty["hub_class_labels"] = list(payload["chart_labels"])
    empty["hub_class_counts"] = list(payload["chart_counts"])

    if not has_feature_access(school, "attendance"):
        empty["hub_attendance_unavailable_message"] = "Enable attendance in your plan to see the 7-day trend."
        return empty

    today = timezone.localdate()
    total_students = Student.objects.filter(user__school=school).count()
    short_labels: list[str] = []
    full_labels: list[str] = []
    pcts: list[float] = []
    presents: list[int] = []

    try:
        if getattr(connection, "needs_rollback", False):
            connection.rollback()
    except Exception:
        pass

    def append_day(d, pct: float, present: int) -> None:
        short_labels.append(d.strftime("%a"))
        full_labels.append(d.strftime("%a %d %b"))
        pcts.append(round(pct, 1))
        presents.append(present)

    try:
        if not total_students:
            for i in range(6, -1, -1):
                d = today - timedelta(days=i)
                append_day(d, 0.0, 0)
        else:
            for i in range(6, -1, -1):
                d = today - timedelta(days=i)
                pres = Attendance.objects.filter(
                    date=d,
                    status=Attendance.Status.PRESENT,
                    student__user__school=school,
                ).count()
                pct = (pres / total_students) * 100
                append_day(d, pct, pres)
        empty["hub_show_attendance_chart"] = True
    except (ProgrammingError, InternalError, DatabaseError):
        try:
            connection.rollback()
        except Exception:
            pass
        short_labels.clear()
        full_labels.clear()
        pcts.clear()
        presents.clear()
        empty["hub_attendance_unavailable_message"] = "Could not load attendance data."
        empty["hub_show_attendance_chart"] = False

    empty["hub_attendance_short_labels"] = short_labels
    empty["hub_attendance_full_labels"] = full_labels
    empty["hub_attendance_pcts"] = pcts
    empty["hub_attendance_present"] = presents
    return empty
