"""
Per-student analytics for the school reports dashboard (/school/reports/).
Read-only aggregates for UI: top student, search list, line-chart series, leaderboard.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from django.db import connection
from django.db.models import Count, Q, Sum
from django.db.utils import DatabaseError, InternalError, ProgrammingError

from apps.core.utils import has_feature_access
from apps.school_data.models import Attendance, Exam, Marks, Student


def _rollback() -> None:
    try:
        if getattr(connection, "needs_rollback", False):
            connection.rollback()
    except Exception:
        pass


def _admission_label(st: Student) -> str:
    raw = (st.admission_number or "").strip()
    if raw:
        return raw
    return (st.roll_number or "").strip() or "—"


def _student_display_name(st: Student) -> str:
    return st.user.get_full_name() or st.user.username or f"Student #{st.pk}"


def _monday_of_week(d: date) -> date:
    """Week bucket starts Monday (matches school Mon–Sat focus)."""
    return d - timedelta(days=d.weekday())


def _week_trend_label(week_start: date) -> str:
    """Compact label for the 7-day window starting Monday."""
    week_end = week_start + timedelta(days=6)
    if week_start.month == week_end.month and week_start.year == week_end.year:
        return f"{week_start.day}–{week_end.day} {week_start.strftime('%b %y')}"
    return f"{week_start.strftime('%d %b')}–{week_end.strftime('%d %b %y')}"


def build_student_insights_context(
    school,
    *,
    user,
    request,
    analytics_scope: dict | None = None,
) -> dict[str, Any]:
    """
    Context keys for student-first analytics (template + json_script bundle).
    Does not alter other dashboard services.
    """
    scope = analytics_scope or {}
    date_from: date = scope.get("date_from")
    date_to: date = scope.get("date_to")
    if date_from is None or date_to is None:
        from django.utils import timezone

        t = timezone.localdate()
        date_from = date_to = t

    empty: dict[str, Any] = {
        "student_insights_enabled": False,
        "student_insights_selected_id": None,
        "student_insights_profile": None,
        "student_insights_leaderboard": [],
        "student_insights_bundle": {},
    }

    if not school or not has_feature_access(school, "reports", user=user):
        return empty

    stu_base = Student.objects.filter(user__school=school).select_related("user")
    total_n = stu_base.count()
    if total_n < 1:
        return empty

    exams_ok = has_feature_access(school, "exams", user=user)
    att_ok = has_feature_access(school, "attendance", user=user)

    try:
        # --- School-wide aggregates for ranking ---
        marks_pct_by_sid: dict[int, float | None] = {}
        exam_count_by_sid: dict[int, int] = {}
        if exams_ok:
            agg = (
                Marks.objects.filter(
                    student__user__school=school,
                    exam__isnull=False,
                    total_marks__gt=0,
                )
                .values("student_id")
                .annotate(
                    to=Sum("marks_obtained"),
                    tm=Sum("total_marks"),
                    ec=Count("exam_id", distinct=True),
                )
            )
            for row in agg:
                sid = int(row["student_id"])
                tm = int(row["tm"] or 0)
                to = int(row["to"] or 0)
                ec = int(row["ec"] or 0)
                exam_count_by_sid[sid] = ec
                marks_pct_by_sid[sid] = round(100.0 * to / tm, 1) if tm > 0 else None

        att_by_sid: dict[int, float | None] = {}
        if att_ok:
            for row in (
                Attendance.objects.filter(
                    student__user__school=school,
                    date__gte=date_from,
                    date__lte=date_to,
                )
                .values("student_id")
                .annotate(
                    tot=Count("id"),
                    pre=Count("id", filter=Q(status=Attendance.Status.PRESENT)),
                )
            ):
                sid = int(row["student_id"])
                tot = int(row["tot"] or 0)
                pre = int(row["pre"] or 0)
                att_by_sid[sid] = round(100.0 * pre / tot, 1) if tot > 0 else None

        rows_rank: list[dict[str, Any]] = []
        for st in stu_base.order_by("user__last_name", "user__first_name", "pk"):
            sid = st.pk
            mp = marks_pct_by_sid.get(sid)
            ec = exam_count_by_sid.get(sid, 0)
            ap = att_by_sid.get(sid)
            m_norm = float(mp) if mp is not None else 0.0
            a_norm = float(ap) if ap is not None else 0.0
            composite = m_norm * 0.5 + a_norm * 0.5
            rows_rank.append(
                {
                    "student": st,
                    "avg_marks_pct": mp,
                    "att_pct": ap,
                    "exams_taken": ec,
                    "composite": composite,
                }
            )

        rows_rank.sort(key=lambda x: (-x["composite"], -(x["avg_marks_pct"] or 0), -(x["att_pct"] or 0)))

        for i, r in enumerate(rows_rank, start=1):
            r["rank"] = i

        # --- Selected student (GET or default top) ---
        selected_id: int | None = None
        if request is not None:
            raw = request.GET.get("student")
            if raw and str(raw).isdigit():
                cand = int(raw)
                if stu_base.filter(pk=cand).exists():
                    selected_id = cand

        if selected_id is None and rows_rank:
            selected_id = rows_rank[0]["student"].pk

        search_options: list[dict[str, Any]] = []
        for r in rows_rank:
            st = r["student"]
            adm = _admission_label(st)
            nm = _student_display_name(st)
            search_options.append(
                {
                    "id": st.pk,
                    "text": f"{nm} - {adm}",
                    "name": nm,
                    "admissionId": adm,
                }
            )

        leaderboard = [
            {
                "id": r["student"].pk,
                "name": _student_display_name(r["student"]),
                "avgPct": r["avg_marks_pct"],
                "attPct": r["att_pct"],
                "rank": r["rank"],
            }
            for r in rows_rank[:5]
        ]

        profile: dict[str, Any] | None = None
        att_labels: list[str] = []
        att_pcts: list[float | None] = []
        exam_labels: list[str] = []
        exam_pcts: list[float | None] = []
        subjects: list[dict[str, Any]] = []

        sel_row = next((r for r in rows_rank if r["student"].pk == selected_id), None)
        if sel_row and selected_id is not None:
            st = sel_row["student"]
            profile = {
                "id": st.pk,
                "name": _student_display_name(st),
                "admissionId": _admission_label(st),
                "attendancePct": sel_row["att_pct"],
                "avgMarksPct": sel_row["avg_marks_pct"],
                "rank": sel_row["rank"],
                "examsTaken": sel_row["exams_taken"],
                "isTopper": sel_row["rank"] == 1,
                "className": st.classroom.name if st.classroom else "—",
            }

            if att_ok:
                week_tot_pre: dict[date, list[int]] = defaultdict(lambda: [0, 0])
                for row in Attendance.objects.filter(
                    student_id=selected_id,
                    date__gte=date_from,
                    date__lte=date_to,
                ).values("date", "status"):
                    d = row["date"]
                    monday = _monday_of_week(d)
                    week_tot_pre[monday][0] += 1
                    if row["status"] == Attendance.Status.PRESENT:
                        week_tot_pre[monday][1] += 1
                for monday in sorted(week_tot_pre.keys()):
                    tot, pre = week_tot_pre[monday][0], week_tot_pre[monday][1]
                    label = _week_trend_label(monday)
                    pct = round(100.0 * pre / tot, 1) if tot > 0 else None
                    att_labels.append(label)
                    att_pcts.append(pct)

            if exams_ok:
                exam_groups: dict[int, list[tuple[int, int]]] = defaultdict(list)
                for m in (
                    Marks.objects.filter(
                        student_id=selected_id,
                        exam__isnull=False,
                        total_marks__gt=0,
                    )
                    .select_related("exam")
                    .order_by("exam__date", "exam_id")
                ):
                    if m.exam_id:
                        exam_groups[m.exam_id].append((int(m.marks_obtained), int(m.total_marks)))

                exam_ids = list(exam_groups.keys())
                exams_map = {e.pk: e for e in Exam.objects.filter(pk__in=exam_ids)}

                def _exam_sort_key(eid: int) -> tuple:
                    ex = exams_map.get(eid)
                    if ex and ex.date:
                        return (ex.date, ex.name or "")
                    return (date.min, "")

                for eid in sorted(exam_ids, key=_exam_sort_key):
                    pairs = exam_groups[eid]
                    to = sum(p[0] for p in pairs)
                    tm = sum(p[1] for p in pairs)
                    ex = exams_map.get(eid)
                    if ex:
                        d = ex.date
                        label = f"{ex.name or 'Exam'}"
                        if d:
                            label = f"{label} ({d.strftime('%d %b %y')})"
                    else:
                        label = f"Exam #{eid}"
                    exam_labels.append(label)
                    exam_pcts.append(round(100.0 * to / tm, 1) if tm > 0 else None)

                for row in (
                    Marks.objects.filter(student_id=selected_id, total_marks__gt=0)
                    .values("subject__name")
                    .annotate(
                        to=Sum("marks_obtained"),
                        tm=Sum("total_marks"),
                    )
                    .order_by("subject__name")
                ):
                    name = (row["subject__name"] or "—").strip() or "—"
                    tm = int(row["tm"] or 0)
                    to = int(row["to"] or 0)
                    subjects.append(
                        {
                            "name": name,
                            "pct": round(100.0 * to / tm, 1) if tm > 0 else None,
                        }
                    )

        bundle: dict[str, Any] = {
            "selectedId": selected_id,
            "searchOptions": search_options,
            "leaderboard": leaderboard,
            "profile": profile,
            "attendanceTrend": {"labels": att_labels, "pcts": att_pcts},
            "examTrend": {"labels": exam_labels, "pcts": exam_pcts},
            "subjects": subjects,
            "hasExams": exams_ok,
            "hasAttendance": att_ok,
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
        }

        return {
            "student_insights_enabled": True,
            "student_insights_selected_id": selected_id,
            "student_insights_profile": profile,
            "student_insights_leaderboard": leaderboard,
            "student_insights_bundle": bundle,
        }
    except (ProgrammingError, InternalError, DatabaseError, TypeError, ValueError):
        _rollback()
        return empty

