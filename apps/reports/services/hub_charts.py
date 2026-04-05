"""
Preview chart data for the main /school/reports/ dashboard (hub).
Attendance trend respects analytics date range + class/section/year filters.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.db import connection
from django.db.utils import DatabaseError, InternalError, ProgrammingError
from django.utils import timezone

from apps.core.utils import has_feature_access
from apps.school_data.models import Attendance, Student

from .analytics_scope import attendance_student_q
from .students_by_class import get_students_by_class_data


def _bucket_ranges(d0: date, d1: date) -> list[tuple[date, date, str, str]]:
    """(bucket_start, bucket_end, short_label, full_label)."""
    n = (d1 - d0).days + 1
    out: list[tuple[date, date, str, str]] = []
    if n <= 62:
        d = d0
        while d <= d1:
            short = d.strftime("%a %d")
            full = d.strftime("%a %d %b %Y")
            out.append((d, d, short, full))
            d += timedelta(days=1)
    else:
        d = d0
        while d <= d1:
            end = min(d + timedelta(days=6), d1)
            short = d.strftime("%d %b") + " – " + end.strftime("%d %b")
            full = short + " " + str(d.year)
            out.append((d, end, short, full))
            d = end + timedelta(days=1)
    return out


def build_hub_chart_context(
    school,
    *,
    user=None,
    analytics_scope: dict | None = None,
) -> dict:
    """
    Students-by-class + attendance trend for selected analytics scope.
    """
    analytics_scope = analytics_scope or {}
    date_from: date | None = analytics_scope.get("date_from")
    date_to: date | None = analytics_scope.get("date_to")
    classroom_id = analytics_scope.get("classroom_id")
    section_id = analytics_scope.get("section_id")
    academic_year_id = analytics_scope.get("academic_year_id")

    empty: dict = {
        "hub_class_labels": [],
        "hub_class_counts": [],
        "hub_attendance_short_labels": [],
        "hub_attendance_full_labels": [],
        "hub_attendance_pcts": [],
        "hub_attendance_present": [],
        "hub_attendance_day_hints": [],
        "hub_show_attendance_chart": False,
        "hub_attendance_unavailable_message": None,
    }
    if not school or not has_feature_access(school, "reports", user=user):
        return empty

    try:
        payload = get_students_by_class_data(school, academic_year_id)
        empty["hub_class_labels"] = list(payload["chart_labels"])
        empty["hub_class_counts"] = list(payload["chart_counts"])
    except (DatabaseError, InternalError, ProgrammingError):
        try:
            connection.rollback()
        except Exception:
            pass

    if not has_feature_access(school, "attendance", user=user):
        empty["hub_attendance_unavailable_message"] = (
            "Enable attendance for this school to see the attendance trend."
        )
        return empty

    today = timezone.localdate()
    if date_from is None or date_to is None:
        date_from = today - timedelta(days=6)
        date_to = today

    stu_q = Student.objects.filter(user__school=school)
    if classroom_id:
        stu_q = stu_q.filter(classroom_id=classroom_id)
    if section_id:
        stu_q = stu_q.filter(section_id=section_id)
    if academic_year_id:
        stu_q = stu_q.filter(academic_year_id=academic_year_id)
    cohort_count = stu_q.count()

    short_labels: list[str] = []
    full_labels: list[str] = []
    pcts: list[float | None] = []
    presents: list[int | None] = []
    hints: list[str] = []

    try:
        if getattr(connection, "needs_rollback", False):
            connection.rollback()
    except Exception:
        pass

    base_att_q = attendance_student_q(
        school,
        classroom_id=classroom_id,
        section_id=section_id,
        academic_year_id=academic_year_id,
    )

    try:
        if not cohort_count:
            for b0, b1, sl, fl in _bucket_ranges(date_from, date_to):
                short_labels.append(sl)
                full_labels.append(fl)
                pcts.append(0.0)
                presents.append(0)
                hints.append("No students in filter")
            empty["hub_show_attendance_chart"] = True
        else:
            for b0, b1, sl, fl in _bucket_ranges(date_from, date_to):
                if b0 == b1 and b0.weekday() == 6:
                    short_labels.append(sl)
                    full_labels.append(fl)
                    pcts.append(None)
                    presents.append(None)
                    hints.append("No school")
                    continue

                marks = Attendance.objects.filter(
                    base_att_q,
                    date__gte=b0,
                    date__lte=b1,
                )
                tot = marks.count()
                pres = marks.filter(status=Attendance.Status.PRESENT).count()
                if tot < 1:
                    short_labels.append(sl)
                    full_labels.append(fl)
                    pcts.append(None)
                    presents.append(None)
                    hints.append("No marks")
                else:
                    short_labels.append(sl)
                    full_labels.append(fl)
                    pcts.append(round(100.0 * pres / tot, 1))
                    presents.append(pres)
                    hints.append(str(pres) + " present of " + str(tot) + " marks")
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
        hints.clear()
        empty["hub_attendance_unavailable_message"] = "Could not load attendance data."
        empty["hub_show_attendance_chart"] = False

    empty["hub_attendance_short_labels"] = short_labels
    empty["hub_attendance_full_labels"] = full_labels
    empty["hub_attendance_pcts"] = pcts
    empty["hub_attendance_present"] = presents
    empty["hub_attendance_day_hints"] = hints
    return empty
