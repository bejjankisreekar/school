"""
Aggregates for the Student Reports dashboard (charts + admission KPIs).
"""
from __future__ import annotations

from calendar import month_abbr
from collections import defaultdict
from datetime import date, timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.core.utils import get_active_academic_year_obj, get_current_academic_year_bounds
from apps.school_data.models import ClassRoom, Student


def _student_qs(school):
    return Student.objects.filter(user__school=school)


def build_student_analytics_context(school) -> dict:
    qs = _student_qs(school)

    # --- 1. Students by class (bar) ---
    class_rows = list(
        ClassRoom.objects.annotate(
            total=Count("students", filter=Q(students__user__school=school))
        )
        .values("name", "total")
        .order_by("name")
    )
    unassigned = qs.filter(classroom__isnull=True).count()
    if unassigned:
        class_rows.append({"name": "Unassigned", "total": unassigned})
    class_labels = [r["name"] or "—" for r in class_rows]
    class_counts = [int(r["total"] or 0) for r in class_rows]

    # --- 2. Students by section: "Class — Section" (bar, horizontal-friendly) ---
    section_rows = list(
        qs.exclude(classroom__isnull=True)
        .exclude(section__isnull=True)
        .values("classroom__name", "section__name")
        .annotate(total=Count("id"))
        .order_by("classroom__name", "section__name")
    )
    section_labels = [f"{r['classroom__name']} — {r['section__name']}" for r in section_rows]
    section_counts = [int(r["total"] or 0) for r in section_rows]
    only_class = (
        qs.filter(classroom__isnull=False, section__isnull=True)
        .values("classroom__name")
        .annotate(total=Count("id"))
        .order_by("classroom__name")
    )
    for r in only_class:
        section_labels.append(f"{r['classroom__name']} — (no section)")
        section_counts.append(int(r["total"] or 0))

    # --- 3. Gender distribution (pie) — Student model has no gender field; show enrolled total ---
    total_enrolled = qs.count()
    gender_labels = ["Enrolled"] if total_enrolled else ["No students"]
    gender_counts = [total_enrolled] if total_enrolled else [0]

    # --- 4. Admission trend: last 12 months (by record created_on) ---
    now = timezone.now()
    start_12m = now - timedelta(days=370)
    trend_raw = list(
        qs.filter(created_on__gte=start_12m)
        .annotate(m=TruncMonth("created_on"))
        .values("m")
        .annotate(c=Count("id"))
        .order_by("m")
    )
    trend_map: dict[tuple[int, int], int] = defaultdict(int)
    for row in trend_raw:
        dt = row["m"]
        if dt is not None:
            trend_map[(dt.year, dt.month)] = int(row["c"] or 0)

    trend_labels: list[str] = []
    trend_counts: list[int] = []
    y, m = now.year, now.month
    for _ in range(12):
        trend_labels.insert(0, f"{month_abbr[m]} {y}")
        trend_counts.insert(0, trend_map.get((y, m), 0))
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    # --- 5. Current academic year: new admissions + this month + mini chart ---
    active_ay = get_active_academic_year_obj()
    if active_ay:
        ay_start, ay_end = active_ay.start_date, active_ay.end_date
        ay_label = active_ay.name
        in_ay = qs.filter(
            Q(academic_year=active_ay)
            | (
                Q(academic_year__isnull=True)
                & Q(created_on__date__gte=ay_start)
                & Q(created_on__date__lte=ay_end)
            )
        )
    else:
        ay_start, ay_end = get_current_academic_year_bounds()
        ay_label = "Current academic year (calendar)"
        in_ay = qs.filter(created_on__date__gte=ay_start, created_on__date__lte=ay_end)

    new_admissions_total = in_ay.count()
    today_d = timezone.localdate()
    month_start = date(today_d.year, today_d.month, 1)
    new_admissions_this_month = in_ay.filter(created_on__date__gte=month_start).count()
    month_start_label = month_start.strftime("%d %b %Y")

    # Mini chart: monthly counts within academic year window (cap 12 months)
    ay_month_labels: list[str] = []
    ay_month_counts: list[int] = []
    cur = ay_start.replace(day=1)
    end_cap = min(today_d, ay_end)
    while cur <= end_cap and len(ay_month_labels) < 12:
        ay_month_labels.append(f"{month_abbr[cur.month]} {cur.year}")
        ay_month_counts.append(
            in_ay.filter(created_on__year=cur.year, created_on__month=cur.month).count()
        )
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)

    return {
        "class_labels": class_labels,
        "class_counts": class_counts,
        "section_labels": section_labels,
        "section_counts": section_counts,
        "gender_labels": gender_labels,
        "gender_counts": gender_counts,
        "trend_labels": trend_labels,
        "trend_counts": trend_counts,
        "ay_label": ay_label,
        "new_admissions_total": new_admissions_total,
        "new_admissions_this_month": new_admissions_this_month,
        "ay_month_labels": ay_month_labels,
        "ay_month_counts": ay_month_counts,
        "month_start_label": month_start_label,
    }
