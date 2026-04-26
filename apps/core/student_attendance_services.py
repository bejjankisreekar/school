from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone

from apps.school_data.classroom_ordering import ORDER_AY_PK_GRADE_NAME
from apps.school_data.models import Attendance, ClassRoom, ClassSectionSubjectTeacher, Section, Student

User = get_user_model()


@dataclass(frozen=True)
class AllowedScope:
    classroom_ids: tuple[int, ...]
    section_ids: tuple[int, ...]
    allowed_pairs: tuple[tuple[int, int], ...]


def _teacher_allowed_scope(user: User) -> AllowedScope:
    teacher = getattr(user, "teacher_profile", None)
    if not teacher:
        return AllowedScope(classroom_ids=(), section_ids=(), allowed_pairs=())

    pairs = (
        ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
        .values_list("class_obj_id", "section_id")
        .distinct()
    )
    allowed_pairs = tuple((int(c), int(s)) for (c, s) in pairs)
    classroom_ids = tuple(sorted({c for (c, _) in allowed_pairs}))
    section_ids = tuple(sorted({s for (_, s) in allowed_pairs}))
    return AllowedScope(classroom_ids=classroom_ids, section_ids=section_ids, allowed_pairs=allowed_pairs)


def _parse_date(val: str | None, default: date) -> date:
    s = (val or "").strip()
    if not s:
        return default
    try:
        return date.fromisoformat(s)
    except Exception:
        return default


def _month_range(month_yyyy_mm: str | None) -> tuple[date, date]:
    today = timezone.localdate()
    s = (month_yyyy_mm or "").strip() or today.strftime("%Y-%m")
    try:
        y, m = map(int, s.split("-"))
        first = date(y, m, 1)
        if m == 12:
            last = date(y, 12, 31)
        else:
            last = date(y, m + 1, 1) - timedelta(days=1)
        return first, last
    except Exception:
        first = date(today.year, today.month, 1)
        return first, today


def get_student_attendance_summary(
    user: User,
    *,
    day: date | None = None,
    classroom_id: int | None = None,
    section_id: int | None = None,
    month: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    """
    Reusable attendance summary for the student attendance dashboard.

    Role rules:
    - Admin: can see all classes/sections in this tenant
    - Teacher: only class/section pairs assigned in ClassSectionSubjectTeacher
    """
    day = day or timezone.localdate()

    is_admin = getattr(user, "role", None) == getattr(User, "Roles", object()).ADMIN or getattr(user, "role", None) == "ADMIN"
    is_teacher = getattr(user, "role", None) == getattr(User, "Roles", object()).TEACHER or getattr(user, "role", None) == "TEACHER"

    allowed = _teacher_allowed_scope(user) if is_teacher and not is_admin else None

    # Resolve range
    if start_date and end_date:
        first, last = (start_date, end_date) if start_date <= end_date else (end_date, start_date)
    else:
        first, last = _month_range(month)

    students_qs = Student.objects.select_related("user", "classroom", "section").all()
    if classroom_id:
        students_qs = students_qs.filter(classroom_id=classroom_id)
    if section_id:
        students_qs = students_qs.filter(section_id=section_id)

    if allowed is not None:
        if classroom_id and section_id:
            if (int(classroom_id), int(section_id)) not in allowed.allowed_pairs:
                students_qs = Student.objects.none()
        else:
            # Scope strictly by allowed (class, section) pairs (not a cartesian product).
            pair_q = Q(pk__isnull=True)
            for (c_id, s_id) in allowed.allowed_pairs:
                pair_q |= Q(classroom_id=c_id, section_id=s_id)
            students_qs = students_qs.filter(pair_q)

    students = list(
        students_qs.order_by(
            "classroom__grade_order",
            "classroom__name",
            "section__name",
            "roll_number",
            "user__first_name",
        )
    )
    student_ids = [s.id for s in students]

    day_att_qs = Attendance.objects.filter(date=day, student_id__in=student_ids).select_related("marked_by")
    att_by_student = {a.student_id: a for a in day_att_qs}

    # Day KPIs
    total_students = len(students)
    present_today = 0
    absent_today = 0
    leave_today = 0
    not_marked = 0
    for s in students:
        a = att_by_student.get(s.id)
        if not a:
            not_marked += 1
        elif a.status == Attendance.Status.PRESENT:
            present_today += 1
        elif a.status == Attendance.Status.ABSENT:
            absent_today += 1
        elif a.status == Attendance.Status.LEAVE:
            leave_today += 1

    attendance_pct = round((present_today / total_students) * 100, 2) if total_students else 0.0

    # Trends: last 7 days (ending at `day`)
    week_start = day - timedelta(days=6)
    week_rows = (
        Attendance.objects.filter(student_id__in=student_ids, date__gte=week_start, date__lte=day)
        .values("date", "status")
        .annotate(cnt=Count("id"))
        .order_by("date")
    )
    week_map: dict[str, dict[str, int]] = {}
    for r in week_rows:
        d_str = r["date"].isoformat()
        week_map.setdefault(d_str, {"PRESENT": 0, "ABSENT": 0, "LEAVE": 0})
        week_map[d_str][r["status"]] = int(r["cnt"])
    week_labels = [(week_start + timedelta(days=i)).isoformat() for i in range(7)]
    week_present = [week_map.get(lbl, {}).get("PRESENT", 0) for lbl in week_labels]
    week_absent = [week_map.get(lbl, {}).get("ABSENT", 0) for lbl in week_labels]

    # Monthly trend: within selected range (group by date)
    range_rows = (
        Attendance.objects.filter(student_id__in=student_ids, date__gte=first, date__lte=last)
        .values("date", "status")
        .annotate(cnt=Count("id"))
        .order_by("date")
    )
    # compute per-day present/total for bar chart (cap to 31 bars in month, fine)
    daily_map: dict[str, dict[str, int]] = {}
    for r in range_rows:
        d_str = r["date"].isoformat()
        daily_map.setdefault(d_str, {"PRESENT": 0, "ABSENT": 0, "LEAVE": 0})
        daily_map[d_str][r["status"]] = int(r["cnt"])
    daily_labels: list[str] = []
    daily_pct: list[float] = []
    d_cur = first
    while d_cur <= last:
        d_str = d_cur.isoformat()
        row = daily_map.get(d_str, {"PRESENT": 0, "ABSENT": 0, "LEAVE": 0})
        marked = row["PRESENT"] + row["ABSENT"] + row["LEAVE"]
        pct = round((row["PRESENT"] / marked) * 100, 2) if marked else 0.0
        daily_labels.append(d_cur.strftime("%d %b"))
        daily_pct.append(pct)
        d_cur += timedelta(days=1)

    # Widgets: lowest attendance students (range)
    per_student = (
        Attendance.objects.filter(student_id__in=student_ids, date__gte=first, date__lte=last)
        .values("student_id")
        .annotate(
            present=Count("id", filter=Q(status=Attendance.Status.PRESENT)),
            total=Count("id"),
        )
    )
    per_student_map = {int(r["student_id"]): r for r in per_student}
    low_students = []
    for s in students:
        agg = per_student_map.get(s.id, {"present": 0, "total": 0})
        total = int(agg.get("total") or 0)
        present = int(agg.get("present") or 0)
        pct = round((present / total) * 100, 2) if total else 0.0
        low_students.append({"student": s, "pct": pct, "total": total})
    low_students = sorted(low_students, key=lambda x: (x["pct"], -x["total"]))[:6]

    # Absent 3+ days (range)
    absent_3p_ids = set(
        Attendance.objects.filter(student_id__in=student_ids, date__gte=first, date__lte=last, status=Attendance.Status.ABSENT)
        .values("student_id")
        .annotate(cnt=Count("id"))
        .filter(cnt__gte=3)
        .values_list("student_id", flat=True)
    )

    # Highest attendance class (range) – based on marked days only
    class_rows = (
        Attendance.objects.filter(student_id__in=student_ids, date__gte=first, date__lte=last)
        .values("student__classroom_id", "student__classroom__name")
        .annotate(
            present=Count("id", filter=Q(status=Attendance.Status.PRESENT)),
            total=Count("id"),
        )
    )
    best_class = None
    best_pct = -1.0
    for r in class_rows:
        total = int(r["total"] or 0)
        present = int(r["present"] or 0)
        pct = (present / total) * 100 if total else 0.0
        if pct > best_pct:
            best_pct = pct
            best_class = {"classroom_id": r["student__classroom_id"], "name": r["student__classroom__name"], "pct": round(pct, 2)}

    # Build table rows (students + status for selected day)
    table_rows = []
    for s in students:
        a = att_by_student.get(s.id)
        table_rows.append(
            {
                "student": s,
                "status": a.status if a else "",
                "marked_by": a.marked_by if a else None,
                "time": timezone.localtime(a.created_at).strftime("%I:%M %p") if a else "",
                "remarks": "",
            }
        )

    # Allowed filters for teacher
    classes = list(ClassRoom.objects.order_by(*ORDER_AY_PK_GRADE_NAME))
    sections = list(Section.objects.order_by("name"))
    if allowed is not None:
        classes = [c for c in classes if c.id in allowed.classroom_ids]
        sections = [s for s in sections if s.id in allowed.section_ids]

    return {
        "day": day,
        "range_start": first,
        "range_end": last,
        "students": students,
        "table_rows": table_rows,
        "kpis": {
            "total_students": total_students,
            "present": present_today,
            "absent": absent_today,
            "leave": leave_today,
            "late_half": 0,
            "attendance_pct": attendance_pct,
            "not_marked": not_marked,
        },
        "charts": {
            "pie": {"present": present_today, "absent": absent_today, "leave": leave_today, "not_marked": not_marked},
            "week": {"labels": week_labels, "present": week_present, "absent": week_absent},
            "range_daily_pct": {"labels": daily_labels, "pct": daily_pct},
        },
        "widgets": {
            "best_class": best_class,
            "low_students": low_students,
            "absent_3p_ids": absent_3p_ids,
            "monthly_pct": round(
                (sum(daily_pct) / len(daily_pct)) if daily_pct else 0.0,
                2,
            ),
        },
        "filters": {
            "classroom_id": classroom_id,
            "section_id": section_id,
            "month": month or "",
            "start_date": first,
            "end_date": last,
        },
        "filter_choices": {"classes": classes, "sections": sections},
        "is_admin": is_admin,
        "is_teacher": is_teacher,
        "allowed_scope": allowed,
    }

