"""
Academic year creation wizard: validate JSON payload, optional copy-from-year, holiday seeding.
Tenant-scoped (run inside ensure_tenant_for_request).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db import transaction

from apps.school_data.models import (
    AcademicYear,
    ClassRoom,
    FeeStructure,
    HolidayCalendar,
    HolidayEvent,
)


def _parse_date(s: Any) -> dt.date | None:
    if s is None or s == "":
        return None
    if isinstance(s, dt.date):
        return s
    try:
        return dt.date.fromisoformat(str(s).strip()[:10])
    except ValueError:
        return None


def ranges_overlap(
    a_start: dt.date, a_end: dt.date, b_start: dt.date, b_end: dt.date
) -> bool:
    return a_start <= b_end and b_start <= a_end


def validate_wizard_payload(
    *,
    name: str,
    start_date: dt.date,
    end_date: dt.date,
    wizard: dict[str, Any],
    exclude_year_id: int | None = None,
) -> str | None:
    """Return error message or None if OK."""
    qs = AcademicYear.objects.filter(name__iexact=name.strip())
    if exclude_year_id:
        qs = qs.exclude(pk=exclude_year_id)
    if qs.exists():
        return "An academic year with this name already exists. Choose a different name."

    oq = AcademicYear.objects.all()
    if exclude_year_id:
        oq = oq.exclude(pk=exclude_year_id)
    for other in oq.only("id", "name", "start_date", "end_date"):
        if ranges_overlap(start_date, end_date, other.start_date, other.end_date):
            return (
                f"This date range overlaps with “{other.name}” "
                f"({other.start_date} – {other.end_date}). Adjust dates or edit the other year."
            )

    terms = wizard.get("terms") if isinstance(wizard.get("terms"), list) else []
    for i, t in enumerate(terms):
        if not isinstance(t, dict):
            continue
        ts = _parse_date(t.get("start"))
        te = _parse_date(t.get("end"))
        if ts and te:
            if te < ts:
                return f"Term {i + 1}: end date must be on or after start date."
            if ts < start_date or te > end_date:
                return f"Term {i + 1}: dates must fall within the academic year range."

    holidays = wizard.get("holidays") if isinstance(wizard.get("holidays"), list) else []
    for i, h in enumerate(holidays):
        if not isinstance(h, dict):
            continue
        hs = _parse_date(h.get("start"))
        he = _parse_date(h.get("end")) or hs
        if hs and he:
            if he < hs:
                return f"Holiday {i + 1}: end date must be on or after start date."
            if hs < start_date or he > end_date:
                return f"Holiday {i + 1}: dates must fall within the academic year range."

    return None


def sanitize_wizard(wizard: dict[str, Any]) -> dict[str, Any]:
    """Keep only expected keys and basic types."""
    out: dict[str, Any] = {}
    if not isinstance(wizard, dict):
        return out

    def _terms(val: Any) -> list[dict[str, str]]:
        if not isinstance(val, list):
            return []
        rows = []
        for t in val[:24]:
            if not isinstance(t, dict):
                continue
            rows.append(
                {
                    "name": str(t.get("name") or "")[:120],
                    "start": str(t.get("start") or "")[:12],
                    "end": str(t.get("end") or "")[:12],
                }
            )
        return rows

    def _holidays(val: Any) -> list[dict[str, str]]:
        if not isinstance(val, list):
            return []
        rows = []
        for h in val[:64]:
            if not isinstance(h, dict):
                continue
            rows.append(
                {
                    "name": str(h.get("name") or "")[:200],
                    "start": str(h.get("start") or "")[:12],
                    "end": str(h.get("end") or "")[:12],
                }
            )
        return rows

    out["terms"] = _terms(wizard.get("terms"))
    out["working_days"] = wizard.get("working_days") if isinstance(wizard.get("working_days"), list) else []
    out["holidays"] = _holidays(wizard.get("holidays"))
    out["promotion_map"] = wizard.get("promotion_map") if isinstance(wizard.get("promotion_map"), list) else []
    out["exam_cycle"] = wizard.get("exam_cycle") if isinstance(wizard.get("exam_cycle"), dict) else {}
    out["fee_cycle"] = str(wizard.get("fee_cycle") or "")[:32]
    out["copy"] = wizard.get("copy") if isinstance(wizard.get("copy"), dict) else {}
    return out


def _iter_dates(d0: dt.date, d1: dt.date):
    d = d0
    while d <= d1:
        yield d
        d += dt.timedelta(days=1)


MAX_HOLIDAY_SPAN_DAYS = 62


@transaction.atomic
def apply_wizard_after_year_created(
    academic_year: AcademicYear,
    user,
    settings: dict[str, Any],
) -> list[str]:
    """
    Run after AcademicYear is saved. Returns human-readable log lines for messages.
    """
    log: list[str] = []
    copy = settings.get("copy") if isinstance(settings.get("copy"), dict) else {}
    from_id = copy.get("from_year_id")
    try:
        from_year_id = int(from_id) if from_id not in (None, "", []) else None
    except (TypeError, ValueError):
        from_year_id = None

    from_year = (
        AcademicYear.objects.filter(pk=from_year_id).first() if from_year_id else None
    )

    class_id_map: dict[int, int] = {}

    if from_year and from_year.pk != academic_year.pk:
        flags = copy.get("flags") if isinstance(copy.get("flags"), dict) else {}
        if flags.get("classes"):
            class_id_map = _copy_classrooms(from_year, academic_year, user)
            log.append(f"Copied {len(class_id_map)} class(es) from {from_year.name}.")
        if flags.get("fee_structure") and class_id_map:
            n = _copy_fee_structures(from_year, academic_year, class_id_map, user)
            if n:
                log.append(f"Copied {n} fee structure row(s).")
        if flags.get("timetable"):
            n = _copy_schedule_profiles(from_year, academic_year)
            if n:
                log.append(f"Copied {n} timetable profile(s) with time slots.")
        if flags.get("sections") and not flags.get("classes"):
            log.append("Sections are shared across years; enable “Copy classes” to attach them to new classes.")
        if flags.get("subjects"):
            log.append("Subjects are school-wide; nothing to duplicate per year.")
        if flags.get("exam_structure"):
            log.append("Exam structure copy is not automated yet — create exams under the new year when classes exist.")
        if flags.get("payroll_rules"):
            log.append("Payroll rules are stored in wizard settings for reference; no payroll rows were duplicated.")

    # Holiday calendar + single-day events (model constraint)
    holidays = settings.get("holidays") if isinstance(settings.get("holidays"), list) else []
    if holidays:
        cal = HolidayCalendar.objects.filter(academic_year=academic_year).first()
        if not cal:
            cal = HolidayCalendar(
                academic_year=academic_year,
                name=f"{academic_year.name} — official calendar",
                is_published=False,
            )
            cal.save_with_audit(user)
        count_ev = 0
        for h in holidays:
            if not isinstance(h, dict):
                continue
            name = (h.get("name") or "").strip() or "Holiday"
            hs = _parse_date(h.get("start"))
            he = _parse_date(h.get("end")) or hs
            if not hs or not he:
                continue
            if (he - hs).days + 1 > MAX_HOLIDAY_SPAN_DAYS:
                he = hs + dt.timedelta(days=MAX_HOLIDAY_SPAN_DAYS - 1)
                log.append(
                    f"Holiday “{name}” truncated to {MAX_HOLIDAY_SPAN_DAYS} days (model uses single-day events)."
                )
            for d in _iter_dates(hs, he):
                ev = HolidayEvent(
                    calendar=cal,
                    name=name if hs == he else f"{name} ({d.isoformat()})",
                    holiday_type=HolidayEvent.HolidayType.VACATION,
                    start_date=d,
                    end_date=d,
                    applies_to=HolidayEvent.AppliesTo.BOTH,
                    description="",
                    recurring_yearly=False,
                )
                ev.save_with_audit(user)
                count_ev += 1
        if count_ev:
            log.append(f"Added {count_ev} holiday day(s) to the calendar (unpublished).")

    return log


def _copy_classrooms(from_ay: AcademicYear, to_ay: AcademicYear, user) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for old in ClassRoom.objects.filter(academic_year=from_ay).prefetch_related("sections"):
        new = ClassRoom(
            name=old.name,
            description=old.description,
            capacity=old.capacity,
            academic_year=to_ay,
            active_schedule_profile=None,
        )
        new.save_with_audit(user)
        new.sections.set(old.sections.all())
        mapping[old.pk] = new.pk
    return mapping


def _copy_fee_structures(
    from_ay: AcademicYear,
    to_ay: AcademicYear,
    class_id_map: dict[int, int],
    user,
) -> int:
    n = 0
    for fs in FeeStructure.objects.filter(academic_year=from_ay).select_related("fee_type"):
        new_cid = None
        if fs.classroom_id:
            new_cid = class_id_map.get(fs.classroom_id)
            if new_cid is None:
                continue
        nf = FeeStructure(
            fee_type=fs.fee_type,
            line_name=fs.line_name,
            classroom_id=new_cid,
            section=fs.section,
            amount=fs.amount,
            academic_year=to_ay,
            frequency=fs.frequency,
            due_day_of_month=fs.due_day_of_month,
            first_due_date=fs.first_due_date,
            installments_enabled=fs.installments_enabled,
            late_fine_rule=fs.late_fine_rule,
            discount_allowed=fs.discount_allowed,
            is_active=fs.is_active,
        )
        nf.save_with_audit(user)
        n += 1
    return n


def _copy_schedule_profiles(from_ay: AcademicYear, to_ay: AcademicYear) -> int:
    from apps.timetable.models import ScheduleProfile, TimeSlot

    count = 0
    for prof in ScheduleProfile.objects.filter(academic_year=from_ay).prefetch_related("time_slots"):
        new_p = ScheduleProfile.objects.create(
            name=prof.name,
            description=prof.description,
            academic_year=to_ay,
            is_active=prof.is_active,
            default_start_time=prof.default_start_time,
            default_end_time=prof.default_end_time,
            total_periods=prof.total_periods,
            break_enabled=prof.break_enabled,
        )
        for slot in prof.time_slots.all():
            TimeSlot.objects.create(
                profile=new_p,
                start_time=slot.start_time,
                end_time=slot.end_time,
                is_break=slot.is_break,
                slot_type=slot.slot_type,
                slot_label=slot.slot_label,
                break_type=slot.break_type,
                order=slot.order,
            )
        count += 1
    return count
