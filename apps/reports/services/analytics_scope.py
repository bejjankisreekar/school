"""
Analytics dashboard date range, academic year, and class/section filters (GET-driven).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from apps.school_data.classroom_ordering import ORDER_GRADE_NAME
from apps.school_data.models import AcademicYear, ClassRoom, Section, Student


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _default_academic_year(
    years: list[AcademicYear], today: date
) -> AcademicYear | None:
    if not years:
        return None
    active = next((y for y in years if y.is_active), None)
    if active:
        return active
    in_range = next(
        (y for y in years if y.start_date <= today <= y.end_date),
        None,
    )
    if in_range:
        return in_range
    return years[0]


def build_analytics_scope_for_request(request, school) -> dict:
    """
    Build filter state from GET (defaults: active / current academic year, full year-to-date range
    clamped to today). Used by hub + dashboard chart services.
    """
    today = timezone.localdate()
    years = list(AcademicYear.objects.order_by("-start_date"))
    year = _default_academic_year(years, today)

    if request is not None:
        raw_y = request.GET.get("academic_year")
        if raw_y and str(raw_y).isdigit():
            yid = int(raw_y)
            picked = next((y for y in years if y.pk == yid), None)
            if picked:
                year = picked

    academic_year_id = year.pk if year else None

    if year:
        date_from = year.start_date
        date_to = min(year.end_date, today)
    else:
        date_from = today - timedelta(days=89)
        date_to = today

    classroom_id: int | None = None
    section_id: int | None = None

    if request is not None:
        df = _parse_date(request.GET.get("date_from"))
        dt = _parse_date(request.GET.get("date_to"))
        if year:
            if df is not None:
                date_from = max(df, year.start_date)
            if dt is not None:
                date_to = min(dt, year.end_date, today)
        else:
            if df is not None:
                date_from = df
            if dt is not None:
                date_to = min(dt, today)
        if date_from > date_to:
            date_from, date_to = date_to, date_from

        rc = request.GET.get("classroom")
        rs = request.GET.get("section")
        if rc and str(rc).isdigit():
            cid = int(rc)
            if ClassRoom.objects.filter(pk=cid).exists():
                classroom_id = cid
        if rs and str(rs).isdigit():
            sid = int(rs)
            if Section.objects.filter(pk=sid).exists():
                section_id = sid

    classrooms = ClassRoom.objects.order_by(*ORDER_GRADE_NAME)
    if academic_year_id:
        classroom_ids = list(
            Student.objects.filter(
                user__school=school,
                academic_year_id=academic_year_id,
                classroom__isnull=False,
            )
            .values_list("classroom_id", flat=True)
            .distinct()
        )
        if classroom_ids:
            classrooms = ClassRoom.objects.filter(pk__in=classroom_ids).order_by(*ORDER_GRADE_NAME)

    sections = Section.objects.order_by("name")
    if classroom_id:
        sid_set = set(
            Student.objects.filter(user__school=school, classroom_id=classroom_id)
            .exclude(section__isnull=True)
            .values_list("section_id", flat=True)
            .distinct()
        )
        if sid_set:
            sections = Section.objects.filter(pk__in=sid_set).order_by("name")

    scope = {
        "date_from": date_from,
        "date_to": date_to,
        "classroom_id": classroom_id,
        "section_id": section_id,
        "academic_year_id": academic_year_id,
    }

    return {
        "analytics_scope": scope,
        "analytics_academic_years": years,
        "analytics_selected_year_id": academic_year_id,
        "analytics_date_from_iso": date_from.isoformat(),
        "analytics_date_to_iso": date_to.isoformat(),
        "analytics_classroom_id": classroom_id,
        "analytics_section_id": section_id,
        "analytics_classrooms": classrooms,
        "analytics_sections": sections,
    }


def attendance_student_q(
    school,
    *,
    classroom_id: int | None = None,
    section_id: int | None = None,
    academic_year_id: int | None = None,
) -> Q:
    """Q object for Attendance rows scoped to school + optional class/section/year."""
    q = Q(student__user__school=school)
    if classroom_id:
        q &= Q(student__classroom_id=classroom_id)
    if section_id:
        q &= Q(student__section_id=section_id)
    if academic_year_id:
        q &= Q(student__academic_year_id=academic_year_id)
    return q
