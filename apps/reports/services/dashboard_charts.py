"""
Extended charts, alerts, and tables for the school reports analytics dashboard.
Uses analytics_scope (date range + class/section/year) for attendance aggregates.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.db import connection
from django.db.models import Count, Q, Sum
from django.db.utils import DatabaseError, InternalError, ProgrammingError
from django.utils import timezone

from apps.core.utils import has_feature_access
from apps.school_data.models import Attendance, ClassRoom, Exam, Marks, Section, Student

from .analytics_scope import attendance_student_q
from .students_by_class import get_students_by_class_data

# Mon–Sat attendance pattern: aggregate all matching calendar weekdays in the analytics range.
_WEEKDAY_SHORT_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _dates_by_weekday_mon_sat(d0: date, d1: date) -> dict[int, list[date]]:
    """Map weekday (Mon=0 .. Sat=5) to dates in [d0, d1]. Sundays excluded."""
    out: dict[int, list[date]] = {i: [] for i in range(6)}
    d = d0
    while d <= d1:
        wd = d.weekday()
        if wd < 6:
            out[wd].append(d)
        d += timedelta(days=1)
    return out


def _weekday_attendance_row(
    base_q,
    dates_by_wd: dict[int, list[date]],
    *,
    classroom_id: int | None,
) -> tuple[list[float | None], list[str]]:
    """One percentage + hint per Mon..Sat for the given scope (optional class filter)."""
    pcts: list[float | None] = []
    hints: list[str] = []
    for wd in range(6):
        dates = dates_by_wd[wd]
        wlab = _WEEKDAY_SHORT_LABELS[wd]
        if not dates:
            pcts.append(None)
            hints.append(f"No {wlab} in selected period")
            continue
        q = Attendance.objects.filter(base_q, date__in=dates)
        if classroom_id is not None:
            q = q.filter(student__classroom_id=classroom_id)
        tot = q.count()
        if tot < 1:
            pcts.append(None)
            hints.append(f"No marks on {wlab}s in period")
        else:
            pres = q.filter(status=Attendance.Status.PRESENT).count()
            p = round(100.0 * pres / tot, 1)
            pcts.append(p)
            nd = len(dates)
            hints.append(f"{pres} present of {tot} marks (all {wlab}s in range, {nd} day(s))")
    return pcts, hints


def _rollback_safely() -> None:
    try:
        if getattr(connection, "needs_rollback", False):
            connection.rollback()
    except Exception:
        pass


def _build_class_student_attendance_rows(
    school,
    date_from,
    date_to,
    classroom_id: int | None,
    section_id: int | None,
    academic_year_id: int | None,
    *,
    limit: int = 150,
) -> list[dict]:
    """Per-student present/total marks in range for one class (classroom_id required)."""
    if not classroom_id:
        return []

    stu_q = Student.objects.filter(
        user__school=school,
        classroom_id=classroom_id,
    ).select_related("user", "classroom", "section")
    if section_id:
        stu_q = stu_q.filter(section_id=section_id)
    if academic_year_id:
        stu_q = stu_q.filter(academic_year_id=academic_year_id)
    stu_q = stu_q.order_by("section__name", "roll_number", "user__last_name")[:limit]

    att_q = attendance_student_q(
        school,
        classroom_id=classroom_id,
        section_id=section_id,
        academic_year_id=academic_year_id,
    )
    stats: dict[int, tuple[int, int]] = {}
    try:
        for row in (
            Attendance.objects.filter(
                att_q,
                date__gte=date_from,
                date__lte=date_to,
            )
            .values("student_id")
            .annotate(
                tot=Count("id"),
                pre=Count("id", filter=Q(status=Attendance.Status.PRESENT)),
            )
        ):
            stats[int(row["student_id"])] = (
                int(row["pre"] or 0),
                int(row["tot"] or 0),
            )
    except (DatabaseError, InternalError, ProgrammingError):
        _rollback_safely()

    rows: list[dict] = []
    for s in stu_q:
        pre, tot = stats.get(s.pk, (0, 0))
        pct = round(100.0 * pre / tot, 1) if tot else None
        rows.append(
            {
                "student_name": s.user.get_full_name() or s.user.username,
                "roll_number": s.roll_number or "—",
                "section_name": s.section.name if s.section else "—",
                "present": pre,
                "total": tot,
                "pct": pct,
            }
        )
    return rows


def extend_dashboard_charts_context(
    school, ctx: dict, *, user=None, analytics_scope: dict | None = None
) -> None:
    """
    Mutates ctx (output of build_hub_chart_context) with extra keys and
    dash_charts_bundle for the dashboard template / Chart.js.
    """
    ctx.setdefault("dash_admission_labels", [])
    ctx.setdefault("dash_admission_counts", [])
    ctx.setdefault("dash_gender_labels", [])
    ctx.setdefault("dash_gender_counts", [])
    ctx.setdefault("dash_attendance_status_labels", [])
    ctx.setdefault("dash_attendance_status_counts", [])
    ctx.setdefault("dash_subject_labels", [])
    ctx.setdefault("dash_subject_avg_pct", [])
    ctx.setdefault("dash_exams_last_90_days", 0)
    ctx.setdefault("dash_alerts", [])
    ctx.setdefault("dash_enrollment_rows", [])
    ctx.setdefault("dash_week_avg_attendance_pct", None)
    ctx.setdefault("dash_has_student_charts", False)
    ctx.setdefault("dash_has_attendance_status_chart", False)
    ctx.setdefault("dash_has_academic_chart", False)
    ctx.setdefault("dash_class_attendance_labels", [])
    ctx.setdefault("dash_class_attendance_pcts", [])
    ctx.setdefault("dash_has_class_attendance_chart", False)
    ctx.setdefault("dash_weekday_short_labels", list(_WEEKDAY_SHORT_LABELS))
    ctx.setdefault("dash_weekday_hub_pcts", [])
    ctx.setdefault("dash_weekday_hub_hints", [])
    ctx.setdefault("dash_class_weekday_pcts", [])
    ctx.setdefault("dash_age_labels", [])
    ctx.setdefault("dash_age_counts", [])
    ctx.setdefault("dash_has_age_chart", False)
    ctx.setdefault("dash_gender_center_primary", "")
    ctx.setdefault("dash_gender_center_sub", "")
    ctx.setdefault("dash_gender_center_extra", "")
    ctx.setdefault("dash_low_attendance_rows", [])
    ctx.setdefault("dash_class_student_attendance_rows", [])
    ctx.setdefault("dash_attendance_mark_count", 0)

    if not school or not has_feature_access(school, "reports", user=user):
        ctx["dash_charts_bundle"] = _bundle_from_ctx(ctx)
        return

    today = timezone.localdate()
    analytics_scope = analytics_scope or {}
    date_from = analytics_scope.get("date_from") or today
    date_to = analytics_scope.get("date_to") or today
    scope_classroom_id = analytics_scope.get("classroom_id")
    scope_section_id = analytics_scope.get("section_id")
    academic_year_id = analytics_scope.get("academic_year_id")

    ctx["dash_admission_labels"] = []
    ctx["dash_admission_counts"] = []

    # --- Gender distribution ---
    try:
        raw = (
            Student.objects.filter(user__school=school)
            .values("gender")
            .annotate(c=Count("id"))
        )
        gmap: dict[str, int] = {row["gender"] or "": int(row["c"] or 0) for row in raw}
        glabels = []
        gcounts = []
        for key, title in (
            ("M", "Male"),
            ("F", "Female"),
            ("O", "Other"),
            ("", "Not specified"),
        ):
            n = int(gmap.get(key, 0))
            if n > 0:
                glabels.append(title)
                gcounts.append(n)
        if not glabels:
            total_st = Student.objects.filter(user__school=school).count()
            if total_st == 0:
                glabels = ["No students yet"]
                gcounts = [1]
            else:
                glabels = ["All students"]
                gcounts = [total_st]
        ctx["dash_gender_labels"] = glabels
        ctx["dash_gender_counts"] = gcounts
        total_g = sum(gcounts)
        if total_g > 0 and glabels and len(glabels) == len(gcounts):
            ordered = sorted(zip(glabels, gcounts), key=lambda x: -x[1])
            top_lab, top_n = ordered[0]
            ctx["dash_gender_center_primary"] = f"{round(100 * top_n / total_g)}%"
            ctx["dash_gender_center_sub"] = top_lab
            if len(ordered) > 1:
                s_lab, s_n = ordered[1]
                ctx["dash_gender_center_extra"] = f"{round(100 * s_n / total_g)}% {s_lab}"
    except (DatabaseError, InternalError, ProgrammingError):
        _rollback_safely()

    ctx["dash_has_student_charts"] = bool(ctx.get("hub_class_labels"))

    # --- Attendance status totals (selected date range + filters) ---
    if has_feature_access(school, "attendance", user=user):
        try:
            att_scope = attendance_student_q(
                school,
                classroom_id=scope_classroom_id,
                section_id=scope_section_id,
                academic_year_id=academic_year_id,
            )
            rows = (
                Attendance.objects.filter(
                    att_scope,
                    date__gte=date_from,
                    date__lte=date_to,
                )
                .values("status")
                .annotate(c=Count("id"))
            )
            smap = {r["status"]: int(r["c"] or 0) for r in rows}
            st_labels = ["Present", "Absent", "On leave"]
            st_counts = [
                smap.get(Attendance.Status.PRESENT, 0),
                smap.get(Attendance.Status.ABSENT, 0),
                smap.get(Attendance.Status.LEAVE, 0),
            ]
            ctx["dash_attendance_status_labels"] = st_labels
            ctx["dash_attendance_status_counts"] = st_counts
            ctx["dash_has_attendance_status_chart"] = sum(st_counts) > 0
            ctx["dash_attendance_mark_count"] = sum(st_counts)
        except (DatabaseError, InternalError, ProgrammingError):
            _rollback_safely()

    raw_hub_pcts = [
        p
        for p in (ctx.get("hub_attendance_pcts") or [])
        if p is not None and isinstance(p, (int, float))
    ]
    ctx["dash_week_avg_attendance_pct"] = (
        round(sum(raw_hub_pcts) / len(raw_hub_pcts), 1) if raw_hub_pcts else None
    )

    # --- Subject averages (school-wide, weighted by marks) ---
    if has_feature_access(school, "exams", user=user):
        try:
            ctx["dash_exams_last_90_days"] = Exam.objects.filter(
                date__gte=today - timedelta(days=90)
            ).count()
            subj_rows = list(
                Marks.objects.filter(
                    student__user__school=school,
                    total_marks__gt=0,
                )
                .values("subject__name")
                .annotate(
                    obtained=Sum("marks_obtained"),
                    tmax=Sum("total_marks"),
                )
            )
            scored: list[tuple[str, float]] = []
            for r in subj_rows:
                tmax = r["tmax"] or 0
                if tmax <= 0:
                    continue
                name = (r["subject__name"] or "—").strip() or "—"
                pct = round(100.0 * float(r["obtained"] or 0) / float(tmax), 1)
                scored.append((name, pct))
            scored.sort(key=lambda x: -x[1])
            ctx["dash_subject_labels"] = [s[0] for s in scored[:12]]
            ctx["dash_subject_avg_pct"] = [s[1] for s in scored[:12]]
            ctx["dash_has_academic_chart"] = bool(ctx["dash_subject_labels"])
        except (DatabaseError, InternalError, ProgrammingError):
            _rollback_safely()

    # --- Enrollment table (top classes) ---
    try:
        payload = get_students_by_class_data(school, academic_year_id)
        rows = [
            {"name": r["name"] or "—", "total": int(r["total"] or 0)}
            for r in payload["class_rows"]
        ]
        rows.sort(key=lambda x: -x["total"])
        total = sum(r["total"] for r in rows) or 1
        ctx["dash_enrollment_rows"] = [
            {
                "rank": i,
                "class_name": r["name"],
                "students": r["total"],
                "share": round(100.0 * r["total"] / total, 1),
            }
            for i, r in enumerate(rows[:12], start=1)
        ]
    except (DatabaseError, InternalError, ProgrammingError):
        _rollback_safely()

    # --- Class attendance comparison (selected range, % present of marks) ---
    if has_feature_access(school, "attendance", user=user):
        try:
            stu_filter = Student.objects.filter(user__school=school, classroom__isnull=False)
            if academic_year_id:
                stu_filter = stu_filter.filter(academic_year_id=academic_year_id)
            if scope_section_id:
                stu_filter = stu_filter.filter(section_id=scope_section_id)
            class_ids = stu_filter.values_list("classroom_id", flat=True).distinct()
            uniq_ids = sorted(set(class_ids))
            cmap = {c.pk: c for c in ClassRoom.objects.filter(pk__in=uniq_ids)}
            labels_ca: list[str] = []
            pcts_ca: list[float] = []
            class_ids_ca: list[int] = []
            base_scope = attendance_student_q(
                school,
                classroom_id=None,
                section_id=scope_section_id,
                academic_year_id=academic_year_id,
            )
            for cid in uniq_ids:
                cls_obj = cmap.get(cid)
                if not cls_obj:
                    continue
                marks = Attendance.objects.filter(
                    base_scope,
                    date__gte=date_from,
                    date__lte=date_to,
                    student__classroom_id=cid,
                )
                tot = marks.count()
                if tot < 1:
                    continue
                pres = marks.filter(status=Attendance.Status.PRESENT).count()
                labels_ca.append(cls_obj.name)
                pcts_ca.append(round(100.0 * pres / tot, 1))
                class_ids_ca.append(cid)
            ctx["dash_class_attendance_labels"] = labels_ca
            ctx["dash_class_attendance_pcts"] = pcts_ca
            ctx["dash_has_class_attendance_chart"] = len(labels_ca) > 0

            dates_by_wd = _dates_by_weekday_mon_sat(date_from, date_to)
            hub_wd_pcts, hub_wd_hints = _weekday_attendance_row(
                base_scope, dates_by_wd, classroom_id=None
            )
            ctx["dash_weekday_short_labels"] = list(_WEEKDAY_SHORT_LABELS)
            ctx["dash_weekday_hub_pcts"] = hub_wd_pcts
            ctx["dash_weekday_hub_hints"] = hub_wd_hints
            class_wd: list[list[float | None]] = []
            for cid in class_ids_ca:
                row_pcts, _ = _weekday_attendance_row(
                    base_scope, dates_by_wd, classroom_id=cid
                )
                class_wd.append(row_pcts)
            ctx["dash_class_weekday_pcts"] = class_wd
        except (DatabaseError, InternalError, ProgrammingError):
            _rollback_safely()
            ctx["dash_weekday_hub_pcts"] = []
            ctx["dash_weekday_hub_hints"] = []
            ctx["dash_class_weekday_pcts"] = []

    try:
        ctx["dash_class_student_attendance_rows"] = _build_class_student_attendance_rows(
            school,
            date_from,
            date_to,
            scope_classroom_id,
            scope_section_id,
            academic_year_id,
        )
    except (DatabaseError, InternalError, ProgrammingError):
        _rollback_safely()
        ctx["dash_class_student_attendance_rows"] = []

    # --- Student age distribution (from date of birth) ---
    try:
        ages: dict[int, int] = {}
        for s in Student.objects.filter(
            user__school=school, date_of_birth__isnull=False
        ).only("date_of_birth"):
            dob = s.date_of_birth
            age = today.year - dob.year - (
                (today.month, today.day) < (dob.month, dob.day)
            )
            if 3 <= age <= 22:
                ages[age] = ages.get(age, 0) + 1
        if ages:
            sorted_ages = sorted(ages.keys())
            ctx["dash_age_labels"] = [f"Age {a}" for a in sorted_ages]
            ctx["dash_age_counts"] = [ages[a] for a in sorted_ages]
            ctx["dash_has_age_chart"] = True
    except (DatabaseError, InternalError, ProgrammingError):
        _rollback_safely()

    # --- Students below attendance threshold (rolling window) ---
    if has_feature_access(school, "attendance", user=user):
        try:
            d0 = today - timedelta(days=75)
            agg = (
                Attendance.objects.filter(
                    date__gte=d0,
                    date__lte=today,
                    student__user__school=school,
                )
                .values("student_id")
                .annotate(
                    total_m=Count("id"),
                    present_m=Count(
                        "id",
                        filter=Q(status=Attendance.Status.PRESENT),
                    ),
                )
                .filter(total_m__gte=8)
            )
            candidates: list[tuple[int, float]] = []
            for row in agg:
                tot = int(row["total_m"] or 0)
                pres = int(row["present_m"] or 0)
                if tot < 1:
                    continue
                pct = round(100.0 * pres / tot, 1)
                if pct < 75.0:
                    candidates.append((int(row["student_id"]), pct))
            candidates.sort(key=lambda x: x[1])
            candidates = candidates[:25]
            if candidates:
                sid_list = [c[0] for c in candidates]
                students = {
                    s.pk: s
                    for s in Student.objects.filter(pk__in=sid_list).select_related(
                        "classroom", "user"
                    )
                }
                low_rows = []
                for sid, pct in candidates:
                    s = students.get(sid)
                    if not s:
                        continue
                    name = s.user.get_full_name() or s.user.username
                    cname = s.classroom.name if s.classroom else "—"
                    raw_phone = (s.parent_phone or "").strip()
                    contact = raw_phone or (getattr(s.user, "email", None) or "") or "—"
                    low_rows.append(
                        {
                            "student_name": name,
                            "class_name": cname,
                            "attendance_pct": pct,
                            "parent_contact": contact,
                        }
                    )
                ctx["dash_low_attendance_rows"] = low_rows
        except (DatabaseError, InternalError, ProgrammingError):
            _rollback_safely()

    # --- Alerts ---
    alerts: list[dict] = []

    try:
        bday_n = Student.objects.filter(
            user__school=school,
            date_of_birth__isnull=False,
            date_of_birth__month=today.month,
            date_of_birth__day=today.day,
        ).count()
        if bday_n:
            alerts.append(
                {
                    "severity": "info",
                    "icon": "bi-cake2",
                    "title": f"{bday_n} student birthday(s) today",
                    "detail": "Share wishes or plan a quick class acknowledgment.",
                }
            )
    except (DatabaseError, InternalError, ProgrammingError):
        _rollback_safely()

    if has_feature_access(school, "attendance", user=user):
        try:
            total_students = Student.objects.filter(user__school=school).count()
            if total_students:
                pair_rows = (
                    Student.objects.filter(
                        user__school=school,
                        classroom__isnull=False,
                        section__isnull=False,
                    )
                    .values("classroom_id", "section_id")
                    .annotate(student_count=Count("id"))
                )
                pending: list[dict] = []
                for row in pair_rows:
                    cid, sid = row["classroom_id"], row["section_id"]
                    n = int(row["student_count"] or 0)
                    marked = Attendance.objects.filter(
                        date=today,
                        student__classroom_id=cid,
                        student__section_id=sid,
                        student__user__school=school,
                    ).count()
                    if marked < n:
                        pending.append(
                            {
                                "classroom_id": cid,
                                "section_id": sid,
                                "marked": marked,
                                "total": n,
                                "missing": n - marked,
                            }
                        )
                if pending:
                    cids = {p["classroom_id"] for p in pending}
                    sids = {p["section_id"] for p in pending}
                    cnames = dict(
                        ClassRoom.objects.filter(pk__in=cids).values_list("id", "name")
                    )
                    snames = dict(
                        Section.objects.filter(pk__in=sids).values_list("id", "name")
                    )
                    pending.sort(key=lambda x: (-x["missing"], x["classroom_id"]))
                    for p in pending[:6]:
                        cname = cnames.get(p["classroom_id"], "Class")
                        sname = snames.get(p["section_id"], "—")
                        alerts.append(
                            {
                                "severity": "warning",
                                "icon": "bi-clipboard2-pulse",
                                "title": "Attendance not fully marked",
                                "detail": f"{cname} · Section {sname}: {p['marked']}/{p['total']} students marked for today.",
                            }
                        )

            wavg = ctx.get("dash_week_avg_attendance_pct")
            if wavg is not None and wavg < 60 and total_students:
                alerts.append(
                    {
                        "severity": "warning",
                        "icon": "bi-graph-down-arrow",
                        "title": "Period attendance below 60%",
                        "detail": f"Average daily presence rate in the selected range is {wavg}%. Review follow-up with class teachers.",
                    }
                )
        except (DatabaseError, InternalError, ProgrammingError):
            _rollback_safely()

    if (
        has_feature_access(school, "exams", user=user)
        and not ctx.get("dash_has_academic_chart")
        and Student.objects.filter(user__school=school).exists()
    ):
        alerts.append(
            {
                "severity": "info",
                "icon": "bi-journal-text",
                "title": "No graded marks yet",
                "detail": "Enter exam marks to unlock subject performance analytics.",
            }
        )

    ctx["dash_alerts"] = alerts
    ctx["dash_charts_bundle"] = _bundle_from_ctx(ctx)


def _bundle_from_ctx(ctx: dict) -> dict:
    """JSON-safe payload for Chart.js (camelCase for JS)."""
    return {
        "hubClassLabels": list(ctx.get("hub_class_labels") or []),
        "hubClassCounts": list(ctx.get("hub_class_counts") or []),
        "hubAttendanceShort": list(ctx.get("hub_attendance_short_labels") or []),
        "hubAttendanceFull": list(ctx.get("hub_attendance_full_labels") or []),
        "hubAttendancePcts": list(ctx.get("hub_attendance_pcts") or []),
        "hubAttendancePresent": list(ctx.get("hub_attendance_present") or []),
        "hubAttendanceDayHints": list(ctx.get("hub_attendance_day_hints") or []),
        "admissionLabels": list(ctx.get("dash_admission_labels") or []),
        "admissionCounts": list(ctx.get("dash_admission_counts") or []),
        "genderLabels": list(ctx.get("dash_gender_labels") or []),
        "genderCounts": list(ctx.get("dash_gender_counts") or []),
        "genderCenterPrimary": ctx.get("dash_gender_center_primary") or "",
        "genderCenterSub": ctx.get("dash_gender_center_sub") or "",
        "genderCenterExtra": ctx.get("dash_gender_center_extra") or "",
        "classAttendanceLabels": list(ctx.get("dash_class_attendance_labels") or []),
        "classAttendancePcts": list(ctx.get("dash_class_attendance_pcts") or []),
        "weekdayShortLabels": list(ctx.get("dash_weekday_short_labels") or []),
        "weekdayHubPcts": list(ctx.get("dash_weekday_hub_pcts") or []),
        "weekdayHubHints": list(ctx.get("dash_weekday_hub_hints") or []),
        "classWeekdayPcts": list(ctx.get("dash_class_weekday_pcts") or []),
        "ageLabels": list(ctx.get("dash_age_labels") or []),
        "ageCounts": list(ctx.get("dash_age_counts") or []),
        "attendanceStatusLabels": list(ctx.get("dash_attendance_status_labels") or []),
        "attendanceStatusCounts": list(ctx.get("dash_attendance_status_counts") or []),
        "subjectLabels": list(ctx.get("dash_subject_labels") or []),
        "subjectAvgPct": list(ctx.get("dash_subject_avg_pct") or []),
        "hubShowAttendance": bool(ctx.get("hub_show_attendance_chart")),
        "weekAvgAttendancePct": ctx.get("dash_week_avg_attendance_pct"),
    }
