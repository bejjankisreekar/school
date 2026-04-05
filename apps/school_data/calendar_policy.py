"""
Academic calendar policy: default Sunday holiday, optional published events and working-Sunday overrides.
Audience: student | teacher (staff attendance uses teacher).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

from django.utils import timezone

if TYPE_CHECKING:
    from apps.school_data.models import HolidayCalendar, HolidayEvent

Audience = Literal["student", "teacher"]


@dataclass(frozen=True)
class DayResolution:
    """Resolved calendar state for one date and audience."""

    is_working_day: bool
    code: str
    """Machine code: working | sunday_holiday | working_sunday_override | holiday_event"""

    label: str
    detail: str
    holiday_type: str | None
    event_names: tuple[str, ...]


def academic_year_for_date(d: date):
    """Return AcademicYear containing ``d``, or None."""
    from apps.school_data.models import AcademicYear

    return (
        AcademicYear.objects.filter(start_date__lte=d, end_date__gte=d)
        .order_by("-start_date")
        .first()
    )


def get_holiday_calendar_for_year(ay) -> HolidayCalendar | None:
    from apps.school_data.models import HolidayCalendar

    if ay is None:
        return None
    return HolidayCalendar.objects.filter(academic_year_id=ay.id).first()


def _applies_to_event(applies: str, audience: Audience) -> bool:
    from apps.school_data.models import HolidayEvent

    if applies == HolidayEvent.AppliesTo.BOTH:
        return True
    if audience == "student":
        return applies == HolidayEvent.AppliesTo.STUDENTS
    return applies == HolidayEvent.AppliesTo.TEACHERS


def _sunday_override_active(cal, d: date, audience: Audience) -> bool:
    from apps.school_data.models import HolidayEvent, WorkingSundayOverride

    if d.weekday() != 6:
        return False
    qs = WorkingSundayOverride.objects.filter(calendar=cal, work_date=d)
    for o in qs:
        if o.applies_to == WorkingSundayOverride.AppliesTo.BOTH:
            return True
        if audience == "student" and o.applies_to == WorkingSundayOverride.AppliesTo.STUDENTS:
            return True
        if audience == "teacher" and o.applies_to == WorkingSundayOverride.AppliesTo.TEACHERS:
            return True
    return False


def _date_in_ay(d: date, ay) -> bool:
    return ay is not None and ay.start_date <= d <= ay.end_date


def _event_covers_date(ev: HolidayEvent, d: date, ay) -> bool:
    if not _date_in_ay(d, ay):
        return False
    if ev.recurring_yearly:
        if ev.start_date != ev.end_date:
            return False
        return d.month == ev.start_date.month and d.day == ev.start_date.day
    return ev.start_date <= d <= ev.end_date


def events_for_date(cal, d: date, ay, audience: Audience) -> list:
    from apps.school_data.models import HolidayEvent

    if not cal or not cal.is_published or ay is None:
        return []
    out = []
    for ev in HolidayEvent.objects.filter(calendar=cal):
        if not _event_covers_date(ev, d, ay):
            continue
        if not _applies_to_event(ev.applies_to, audience):
            continue
        out.append(ev)
    out.sort(key=lambda e: (e.holiday_type, e.name))
    return out


def resolve_day(d: date, audience: Audience, ay=None) -> DayResolution:
    """
    Resolve calendar for date. Built-in: Sunday = non-working (holiday) unless a published
    calendar has a working-Sunday override for this audience. Published holiday events apply
    only when the calendar is published.
    """
    from apps.school_data.models import HolidayEvent

    if ay is None:
        ay = academic_year_for_date(d)

    cal = get_holiday_calendar_for_year(ay) if ay else None

    if cal and cal.is_published and _sunday_override_active(cal, d, audience):
        note = ""
        o = (
            cal.working_sunday_overrides.filter(work_date=d)
            .order_by("id")
            .first()
        )
        if o and o.note:
            note = f" ({o.note})"
        return DayResolution(
            is_working_day=True,
            code="working_sunday_override",
            label="Working Sunday",
            detail=f"This Sunday is marked as a working day{note}".strip(),
            holiday_type=None,
            event_names=(),
        )

    if cal and cal.is_published and ay:
        evs = events_for_date(cal, d, ay, audience)
        if evs:
            primary = evs[0]
            type_display = primary.get_holiday_type_display()
            names = tuple(e.name for e in evs)
            if len(names) == 1:
                detail = f"{names[0]} — {type_display}"
            else:
                detail = " · ".join(names)
            return DayResolution(
                is_working_day=False,
                code="holiday_event",
                label=names[0],
                detail=detail,
                holiday_type=primary.holiday_type,
                event_names=names,
            )

    if d.weekday() == 6:
        return DayResolution(
            is_working_day=False,
            code="sunday_holiday",
            label="Sunday (Holiday)",
            detail="Weekly holiday — attendance not required.",
            holiday_type=None,
            event_names=(),
        )

    return DayResolution(
        is_working_day=True,
        code="working",
        label="Working day",
        detail="",
        holiday_type=None,
        event_names=(),
    )


def allows_attendance_entry(d: date, audience: Audience, ay=None) -> bool:
    r = resolve_day(d, audience, ay=ay)
    return r.is_working_day and d <= date.today()


def count_working_days_in_range(
    start: date,
    end: date,
    audience: Audience,
    *,
    ay=None,
) -> tuple[int, int]:
    """Return (working_days, holiday_days) inclusive between start and end."""
    if start > end:
        return 0, 0
    w = h = 0
    cur = start
    while cur <= end:
        if resolve_day(cur, audience, ay=ay).is_working_day:
            w += 1
        else:
            h += 1
        cur = date.fromordinal(cur.toordinal() + 1)
    return w, h


def upcoming_events(
    cal,
    *,
    from_date: date | None = None,
    limit: int = 12,
    audience: Audience | None = None,
) -> list:
    """Upcoming holiday events from today (or from_date) within academic year span."""
    from apps.school_data.models import HolidayEvent

    if not cal or not cal.is_published:
        return []
    ay = cal.academic_year
    start = from_date or date.today()
    if start > ay.end_date:
        return []
    start = max(start, ay.start_date)
    out = []
    for ev in HolidayEvent.objects.filter(calendar=cal).order_by("start_date", "name"):
        if audience and not _applies_to_event(ev.applies_to, audience):
            continue
        if ev.recurring_yearly and ev.start_date == ev.end_date:
            y = start.year
            cand = date(y, ev.start_date.month, ev.start_date.day)
            if cand < start:
                cand = date(y + 1, ev.start_date.month, ev.start_date.day)
            if ay.start_date <= cand <= ay.end_date and cand >= start:
                out.append((cand, ev))
        else:
            if ev.end_date < start:
                continue
            eff_start = max(ev.start_date, start)
            if eff_start <= ay.end_date and ev.start_date <= ay.end_date:
                out.append((eff_start, ev))
    out.sort(key=lambda x: x[0])
    return out[:limit]


def build_month_cells(
    year: int,
    month: int,
    cal,
    *,
    audience: Audience = "student",
) -> list[dict]:
    """Grid cells for a month view (leading blanks + days), with policy metadata."""
    import calendar

    ay = cal.academic_year if cal else academic_year_for_date(date(year, month, 15))

    first_weekday, days_in_month = calendar.monthrange(year, month)
    # calendar.weekday: Monday=0; template may expect Sunday-first — use Sunday=0 for grid
    leading = (first_weekday + 1) % 7
    cells = []
    for _ in range(leading):
        cells.append({"is_blank": True})
    for dom in range(1, days_in_month + 1):
        d = date(year, month, dom)
        res = resolve_day(d, audience, ay=ay)
        css = "cal-day"
        if res.code == "holiday_event":
            ht = res.holiday_type or ""
            if ht == "NATIONAL":
                css += " cal-national"
            elif ht == "FESTIVAL":
                css += " cal-festival"
            elif ht in ("VACATION", "EXAM_LEAVE", "SCHOOL"):
                css += " cal-school"
            elif ht == "EMERGENCY":
                css += " cal-emergency"
            else:
                css += " cal-special"
        elif res.code == "sunday_holiday":
            css += " cal-sunday"
        elif res.code == "working_sunday_override":
            css += " cal-working-sunday"
        elif res.is_working_day:
            css += " cal-working"
        cells.append(
            {
                "is_blank": False,
                "dom": dom,
                "date": d,
                "iso": d.isoformat(),
                "resolution": res,
                "css": css,
                "title": res.detail or res.label,
            }
        )
    while len(cells) % 7:
        cells.append({"is_blank": True})
    return cells


def ensure_calendar_for_academic_year(ay):
    """Get or create the single HolidayCalendar row for this academic year."""
    from apps.school_data.models import HolidayCalendar

    cal, _ = HolidayCalendar.objects.get_or_create(
        academic_year=ay,
        defaults={"name": f"Holiday calendar — {ay.name}"},
    )
    return cal


def publish_calendar(cal, *, user=None) -> None:
    now = timezone.now()
    cal.is_published = True
    cal.published_at = now
    cal.unpublished_at = None
    if user:
        cal.save_with_audit(user)
    else:
        cal.save(update_fields=["is_published", "published_at", "unpublished_at", "modified_on"])


def portal_holiday_widget_context(audience: Audience) -> dict:
    """Today + upcoming rows for student/teacher/parent dashboards."""
    from apps.school_data.models import AcademicYear

    today = date.today()
    ay = (
        AcademicYear.objects.filter(is_active=True).order_by("-start_date").first()
        or academic_year_for_date(today)
    )
    cal = get_holiday_calendar_for_year(ay)
    return {
        "holiday_today": resolve_day(today, audience, ay=ay),
        "holiday_upcoming": upcoming_events(cal, from_date=today, limit=6, audience=audience),
        "holiday_cal_published": bool(cal and cal.is_published),
    }


def unpublish_calendar(cal, *, user=None) -> None:
    now = timezone.now()
    cal.is_published = False
    cal.unpublished_at = now
    if user:
        cal.save_with_audit(user)
    else:
        cal.save(update_fields=["is_published", "unpublished_at", "modified_on"])
