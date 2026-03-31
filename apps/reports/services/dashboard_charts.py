"""
Extended charts, alerts, and tables for the school reports analytics dashboard.
Builds on hub chart data (students by class, 7-day attendance) with admissions,
gender mix, weekly attendance status, subject performance, and operational alerts.
"""
from __future__ import annotations

from calendar import month_abbr
from datetime import timedelta

from django.db import connection
from django.db.models import Count, Q, Sum
from django.db.utils import DatabaseError, InternalError, ProgrammingError
from django.utils import timezone

from apps.core.utils import has_feature_access
from apps.school_data.models import Attendance, ClassRoom, Exam, Marks, Section, Student

from .students_by_class import get_students_by_class_data


def _rollback_safely() -> None:
    try:
        if getattr(connection, "needs_rollback", False):
            connection.rollback()
    except Exception:
        pass


def _last_n_month_pairs(today, n: int = 6) -> list[tuple[int, int]]:
    y, m = today.year, today.month
    pairs: list[tuple[int, int]] = []
    for _ in range(n):
        pairs.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(pairs))


def extend_dashboard_charts_context(school, ctx: dict, *, user=None) -> None:
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
    ctx.setdefault("dash_age_labels", [])
    ctx.setdefault("dash_age_counts", [])
    ctx.setdefault("dash_has_age_chart", False)
    ctx.setdefault("dash_gender_center_primary", "")
    ctx.setdefault("dash_gender_center_sub", "")
    ctx.setdefault("dash_gender_center_extra", "")
    ctx.setdefault("dash_low_attendance_rows", [])

    if not school or not has_feature_access(school, "reports", user=user):
        ctx["dash_charts_bundle"] = _bundle_from_ctx(ctx)
        return

    today = timezone.localdate()

    # --- Admissions (last 8 months, by calendar month) ---
    try:
        pairs = _last_n_month_pairs(today, 8)
        labels: list[str] = []
        counts: list[int] = []
        for y, m in pairs:
            labels.append(f"{month_abbr[m]} {y}")
            counts.append(
                Student.objects.filter(
                    user__school=school,
                    created_on__year=y,
                    created_on__month=m,
                ).count()
            )
        ctx["dash_admission_labels"] = labels
        ctx["dash_admission_counts"] = counts
        ctx["dash_has_student_charts"] = bool(ctx.get("hub_class_labels")) or sum(counts) > 0
    except (DatabaseError, InternalError, ProgrammingError):
        _rollback_safely()

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

    # --- Weekly attendance status totals (last 7 days) ---
    if has_feature_access(school, "attendance", user=user):
        try:
            d0 = today - timedelta(days=6)
            rows = (
                Attendance.objects.filter(
                    date__gte=d0,
                    date__lte=today,
                    student__user__school=school,
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

            total_students = Student.objects.filter(user__school=school).count()
            if total_students:
                pcts: list[float] = []
                for i in range(6, -1, -1):
                    d = today - timedelta(days=i)
                    if d.weekday() == 6:
                        continue
                    pres = Attendance.objects.filter(
                        date=d,
                        status=Attendance.Status.PRESENT,
                        student__user__school=school,
                    ).count()
                    pcts.append(round((pres / total_students) * 100, 1))
                ctx["dash_week_avg_attendance_pct"] = round(
                    sum(pcts) / len(pcts), 1
                ) if pcts else None
        except (DatabaseError, InternalError, ProgrammingError):
            _rollback_safely()

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
        payload = get_students_by_class_data(school, None)
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

    # --- Class attendance comparison (last 30 days, % of marks that are present) ---
    if has_feature_access(school, "attendance", user=user):
        try:
            d0 = today - timedelta(days=30)
            class_ids = (
                Student.objects.filter(user__school=school, classroom__isnull=False)
                .values_list("classroom_id", flat=True)
                .distinct()
            )
            uniq_ids = sorted(set(class_ids))
            cmap = {c.pk: c for c in ClassRoom.objects.filter(pk__in=uniq_ids)}
            labels_ca: list[str] = []
            pcts_ca: list[float] = []
            for cid in uniq_ids:
                cls_obj = cmap.get(cid)
                if not cls_obj:
                    continue
                marks = Attendance.objects.filter(
                    date__gte=d0,
                    date__lte=today,
                    student__classroom_id=cid,
                    student__user__school=school,
                )
                tot = marks.count()
                if tot < 1:
                    continue
                pres = marks.filter(status=Attendance.Status.PRESENT).count()
                labels_ca.append(cls_obj.name)
                pcts_ca.append(round(100.0 * pres / tot, 1))
            ctx["dash_class_attendance_labels"] = labels_ca
            ctx["dash_class_attendance_pcts"] = pcts_ca
            ctx["dash_has_class_attendance_chart"] = len(labels_ca) > 0
        except (DatabaseError, InternalError, ProgrammingError):
            _rollback_safely()

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
                        "title": "Weekly attendance below 60%",
                        "detail": f"Rolling 7-day average presence is {wavg}%. Review follow-up with class teachers.",
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
        "ageLabels": list(ctx.get("dash_age_labels") or []),
        "ageCounts": list(ctx.get("dash_age_counts") or []),
        "attendanceStatusLabels": list(ctx.get("dash_attendance_status_labels") or []),
        "attendanceStatusCounts": list(ctx.get("dash_attendance_status_counts") or []),
        "subjectLabels": list(ctx.get("dash_subject_labels") or []),
        "subjectAvgPct": list(ctx.get("dash_subject_avg_pct") or []),
        "hubShowAttendance": bool(ctx.get("hub_show_attendance_chart")),
        "weekAvgAttendancePct": ctx.get("dash_week_avg_attendance_pct"),
    }
