"""
Aggregations for production chart reports (/school/reports/chart/<key>/).
Each builder returns a dict for templates/core/reports/chart_report.html.
"""
from __future__ import annotations

from calendar import month_abbr
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.core.utils import has_feature_access
from apps.school_data.models import (
    Attendance,
    ClassRoom,
    ClassSectionSubjectTeacher,
    Exam,
    Homework,
    HomeworkSubmission,
    Marks,
    OnlineAdmission,
    Payment,
    StaffAttendance,
    Student,
    Teacher,
)


def _base_ctx(
    *,
    title: str,
    subtitle: str,
    chart_type: str,
    x_title: str = "",
    y_title: str = "",
    labels: list,
    values: list,
    line_fill: bool = False,
    line_tension: float = 0.35,
    y_min: float | None = None,
    y_max: float | None = None,
    horizontal_bar: bool = False,
    empty_message: str = "No data available for this report yet.",
    error: str | None = None,
) -> dict:
    return {
        "report_title": title,
        "report_subtitle": subtitle,
        "chart_type": chart_type,
        "x_axis_title": x_title,
        "y_axis_title": y_title,
        "chart_labels": list(labels),
        "chart_values": [float(v) if isinstance(v, Decimal) else v for v in values],
        "line_fill": line_fill,
        "line_tension": line_tension,
        "y_min": y_min,
        "y_max": y_max,
        "horizontal_bar": horizontal_bar,
        "empty_message": empty_message,
        "chart_error": error,
    }


def report_students_by_section(school) -> dict:
    qs = (
        Student.objects.filter(user__school=school)
        .exclude(classroom__isnull=True)
        .exclude(section__isnull=True)
        .values("classroom__name", "classroom__grade_order", "section__name")
        .annotate(c=Count("id"))
        .order_by("classroom__grade_order", "classroom__name", "section__name")
    )
    rows = list(qs)
    labels = [f"{r['classroom__name']} — {r['section__name']}" for r in rows]
    values = [int(r["c"] or 0) for r in rows]
    only_class = (
        Student.objects.filter(user__school=school, classroom__isnull=False, section__isnull=True)
        .values("classroom__name", "classroom__grade_order")
        .annotate(c=Count("id"))
        .order_by("classroom__grade_order", "classroom__name")
    )
    for r in only_class:
        labels.append(f"{r['classroom__name']} — (no section)")
        values.append(int(r["c"] or 0))
    return _base_ctx(
        title="Students by Class & Section",
        subtitle="Headcount per class–section combination (bar).",
        chart_type="bar",
        x_title="Class — Section",
        y_title="Students",
        labels=labels,
        values=values,
        horizontal_bar=len(labels) > 8,
    )


def report_gender_distribution(school) -> dict:
    qs = Student.objects.filter(user__school=school)
    total = qs.count()
    male = qs.filter(gender=Student.Gender.MALE).count()
    female = qs.filter(gender=Student.Gender.FEMALE).count()
    other = total - male - female
    labels: list[str] = []
    values: list[int] = []
    if male:
        labels.append("Male")
        values.append(male)
    if female:
        labels.append("Female")
        values.append(female)
    if other:
        labels.append("Other / not specified")
        values.append(other)
    if not labels:
        return _base_ctx(
            title="Student Gender Distribution",
            subtitle="Share of enrolled students by gender (donut).",
            chart_type="doughnut",
            x_title="",
            y_title="",
            labels=[],
            values=[],
            empty_message="No students enrolled yet.",
        )
    return _base_ctx(
        title="Student Gender Distribution",
        subtitle="Share of enrolled students by gender (donut).",
        chart_type="doughnut",
        x_title="",
        y_title="",
        labels=labels,
        values=values,
    )


def report_new_students_trend(school) -> dict:
    now = timezone.now()
    start = now - timedelta(days=370)
    raw = list(
        Student.objects.filter(user__school=school, created_on__gte=start)
        .annotate(m=TruncMonth("created_on"))
        .values("m")
        .annotate(c=Count("id"))
        .order_by("m")
    )
    trend_map: dict[tuple[int, int], int] = defaultdict(int)
    for row in raw:
        dt = row["m"]
        if dt is not None:
            trend_map[(dt.year, dt.month)] = int(row["c"] or 0)
    y, m = now.year, now.month
    labels: list[str] = []
    values: list[int] = []
    for _ in range(12):
        labels.insert(0, f"{month_abbr[m]} {y}")
        values.insert(0, trend_map.get((y, m), 0))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return _base_ctx(
        title="New Student Registrations",
        subtitle="Students added per month (last 12 months, line).",
        chart_type="line",
        x_title="Month",
        y_title="New students",
        labels=labels,
        values=values,
        line_fill=True,
        line_tension=0.35,
        y_min=0,
    )


def _marks_avg_by_dimension(school, dim: str, title: str, subtitle: str, x_label: str) -> dict:
    rows = list(
        Marks.objects.filter(student__user__school=school, exam__isnull=False)
        .values(dim)
        .annotate(total_o=Sum("marks_obtained"), total_m=Sum("total_marks"))
        .order_by(dim)
    )
    labels: list[str] = []
    values: list[float] = []
    for r in rows:
        name = r.get(dim) or "—"
        to, tm = r.get("total_o") or 0, r.get("total_m") or 0
        if tm:
            labels.append(str(name))
            values.append(round(float(to) / float(tm) * 100, 1))
    return _base_ctx(
        title=title,
        subtitle=subtitle,
        chart_type="bar",
        x_title=x_label,
        y_title="Average %",
        labels=labels,
        values=values,
        y_min=0,
        y_max=100,
    )


def report_exam_avg_by_class(school) -> dict:
    return _marks_avg_by_dimension(
        school,
        "student__classroom__name",
        "Class-wise Academic Performance",
        "Average marks % across all recorded exams, by class.",
        "Class",
    )


def report_exam_avg_by_subject(school) -> dict:
    return _marks_avg_by_dimension(
        school,
        "subject__name",
        "Subject-wise Performance",
        "Average percentage scored per subject (all exams).",
        "Subject",
    )


def report_fee_collection_trend(school) -> dict:
    if not has_feature_access(school, "fees"):
        return _base_ctx(
            title="Fee Collection Trend",
            subtitle="Requires fees module on your plan.",
            chart_type="line",
            labels=[],
            values=[],
            empty_message="Fees are not enabled for your school.",
        )
    today = timezone.localdate()
    start = today.replace(day=1) - timedelta(days=365)
    raw = list(
        Payment.objects.filter(fee__student__user__school=school, payment_date__gte=start)
        .annotate(m=TruncMonth("payment_date"))
        .values("m")
        .annotate(total=Sum("amount"))
        .order_by("m")
    )
    month_map: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0"))
    for row in raw:
        dt = row["m"]
        if dt is not None:
            month_map[(dt.year, dt.month)] += row["total"] or Decimal("0")
    y, m = today.year, today.month
    labels: list[str] = []
    values: list[float] = []
    for _ in range(12):
        labels.insert(0, f"{month_abbr[m]} {y}")
        values.insert(0, float(month_map.get((y, m), Decimal("0"))))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return _base_ctx(
        title="Fee Collection by Month",
        subtitle="Total payments recorded (last 12 months).",
        chart_type="bar",
        x_title="Month",
        y_title="Amount collected (₹)",
        labels=labels,
        values=values,
        y_min=0,
    )


def report_attendance_status_today(school) -> dict:
    if not has_feature_access(school, "attendance"):
        return _base_ctx(
            title="Today's Attendance Mix",
            subtitle="Requires attendance module.",
            chart_type="pie",
            labels=[],
            values=[],
            empty_message="Attendance is not enabled for your school.",
        )
    today = timezone.localdate()
    qs = Attendance.objects.filter(student__user__school=school, date=today)
    agg = {row["status"]: row["c"] for row in qs.values("status").annotate(c=Count("id"))}
    order = [
        (Attendance.Status.PRESENT, "Present"),
        (Attendance.Status.ABSENT, "Absent"),
        (Attendance.Status.LEAVE, "Leave"),
    ]
    labels: list[str] = []
    values: list[int] = []
    for code, name in order:
        n = int(agg.get(code, 0))
        if n:
            labels.append(name)
            values.append(n)
    if not labels:
        return _base_ctx(
            title="Today's Attendance Mix",
            subtitle="No attendance marks for today yet.",
            chart_type="pie",
            labels=["No records"],
            values=[1],
            empty_message="Mark attendance to see the distribution.",
        )
    return _base_ctx(
        title="Today's Attendance Mix",
        subtitle=f"Student attendance status for {today.strftime('%d %b %Y')} (pie).",
        chart_type="pie",
        labels=labels,
        values=values,
    )


def report_staff_attendance_week(school) -> dict:
    teachers = Teacher.objects.filter(user__school=school)
    total_staff = teachers.count()
    if total_staff == 0:
        return _base_ctx(
            title="Staff Attendance (7 Days)",
            subtitle="No teachers on record.",
            chart_type="line",
            labels=[],
            values=[],
            empty_message="Add teachers to track staff attendance.",
        )
    today = timezone.localdate()
    labels: list[str] = []
    values: list[float] = []
    teacher_ids = list(teachers.values_list("pk", flat=True))
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        pres = StaffAttendance.objects.filter(
            date=d,
            teacher_id__in=teacher_ids,
            status=StaffAttendance.Status.PRESENT,
        ).count()
        pct = round((pres / total_staff) * 100, 1) if total_staff else 0.0
        labels.append(d.strftime("%a %d %b"))
        values.append(pct)
    return _base_ctx(
        title="Staff Attendance (Last 7 Days)",
        subtitle="Share of teachers marked present each day (line).",
        chart_type="line",
        x_title="Date",
        y_title="Present %",
        labels=labels,
        values=values,
        line_fill=True,
        line_tension=0.4,
        y_min=0,
        y_max=100,
    )


def report_teaching_load_by_subject(school) -> dict:
    rows = list(
        ClassSectionSubjectTeacher.objects.filter(teacher__user__school=school)
        .values("subject__name")
        .annotate(c=Count("id"))
        .order_by("-c")[:20]
    )
    labels = [r["subject__name"] or "—" for r in rows]
    values = [int(r["c"] or 0) for r in rows]
    return _base_ctx(
        title="Teaching Load by Subject",
        subtitle="Number of class–section assignments per subject (horizontal bar).",
        chart_type="bar",
        x_title="Assignments",
        y_title="Subject",
        labels=labels,
        values=values,
        horizontal_bar=True,
    )


def report_homework_completion(school) -> dict:
    recent = list(
        Homework.objects.defer("attachment").order_by("-created_at")[:12]
    )
    labels: list[str] = []
    values: list[float] = []
    for hw in recent:
        subs = hw.submissions.all()
        total = subs.count()
        done = subs.filter(status=HomeworkSubmission.Status.COMPLETED).count()
        pct = round((done / total) * 100, 1) if total else 0.0
        title = (hw.title or "Homework")[:36]
        if len(hw.title or "") > 36:
            title += "…"
        labels.append(title)
        values.append(pct)
    return _base_ctx(
        title="Homework Completion Rate",
        subtitle="Recent assignments: % of submissions marked completed.",
        chart_type="bar",
        x_title="Homework",
        y_title="Completion %",
        labels=labels,
        values=values,
        y_min=0,
        y_max=100,
        horizontal_bar=len(labels) > 6,
    )


def report_exams_by_month(school) -> dict:
    today = timezone.localdate()
    start = today.replace(day=1) - timedelta(days=365)
    raw = list(
        Exam.objects.filter(date__gte=start)
        .annotate(m=TruncMonth("date"))
        .values("m")
        .annotate(c=Count("id"))
        .order_by("m")
    )
    cmap: dict[tuple[int, int], int] = defaultdict(int)
    for row in raw:
        dt = row["m"]
        if dt is not None:
            cmap[(dt.year, dt.month)] = int(row["c"] or 0)
    y, m = today.year, today.month
    labels: list[str] = []
    values: list[int] = []
    for _ in range(12):
        labels.insert(0, f"{month_abbr[m]} {y}")
        values.insert(0, cmap.get((y, m), 0))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return _base_ctx(
        title="Exams Scheduled by Month",
        subtitle="Count of exam records per month (last 12 months).",
        chart_type="bar",
        x_title="Month",
        y_title="Exams",
        labels=labels,
        values=values,
        y_min=0,
    )


def report_student_age_bands(school) -> dict:
    today = timezone.localdate()
    bands = [
        ("Under 8", 0, 7),
        ("8–10", 8, 10),
        ("11–13", 11, 13),
        ("14–16", 14, 16),
        ("17+", 17, 200),
    ]
    labels: list[str] = []
    values: list[int] = []
    students = Student.objects.filter(user__school=school).exclude(date_of_birth__isnull=True)
    for name, lo, hi in bands:
        c = 0
        for s in students.only("date_of_birth"):
            age = today.year - s.date_of_birth.year - (
                (today.month, today.day) < (s.date_of_birth.month, s.date_of_birth.day)
            )
            if lo <= age <= hi:
                c += 1
        labels.append(name)
        values.append(c)
    return _base_ctx(
        title="Student Age Bands",
        subtitle="Enrolled students by approximate age (from date of birth).",
        chart_type="bar",
        x_title="Age band",
        y_title="Students",
        labels=labels,
        values=values,
        y_min=0,
    )


def report_admissions_pipeline(school) -> dict:
    if not school.has_feature("online_admission"):
        return _base_ctx(
            title="Online Admissions Pipeline",
            subtitle="Requires online admissions on your plan.",
            chart_type="doughnut",
            labels=[],
            values=[],
            empty_message="Online admissions are not enabled.",
        )
    rows = list(OnlineAdmission.objects.values("status").annotate(c=Count("id")))
    labels = [dict(OnlineAdmission.Status.choices).get(r["status"], r["status"]) for r in rows]
    values = [int(r["c"] or 0) for r in rows]
    if not labels:
        return _base_ctx(
            title="Online Admissions Pipeline",
            subtitle="Applications by status.",
            chart_type="doughnut",
            labels=["No applications"],
            values=[0],
            empty_message="No online applications yet.",
        )
    return _base_ctx(
        title="Online Admissions Pipeline",
        subtitle="Application counts by status (donut).",
        chart_type="doughnut",
        labels=labels,
        values=values,
    )


CHART_REPORT_BUILDERS: dict[str, dict] = {
    "students-by-section": {"fn": report_students_by_section},
    "gender-distribution": {"fn": report_gender_distribution},
    "new-students-trend": {"fn": report_new_students_trend},
    "exam-avg-by-class": {"fn": report_exam_avg_by_class},
    "exam-avg-by-subject": {"fn": report_exam_avg_by_subject},
    "fee-collection-trend": {"fn": report_fee_collection_trend},
    "attendance-status-today": {"fn": report_attendance_status_today},
    "staff-attendance-week": {"fn": report_staff_attendance_week},
    "teaching-load-by-subject": {"fn": report_teaching_load_by_subject},
    "homework-completion": {"fn": report_homework_completion},
    "exams-by-month": {"fn": report_exams_by_month},
    "student-age-bands": {"fn": report_student_age_bands},
    "admissions-pipeline": {"fn": report_admissions_pipeline},
}


def build_chart_report(school, report_key: str) -> dict | None:
    meta = CHART_REPORT_BUILDERS.get(report_key)
    if not meta:
        return None
    try:
        ctx = meta["fn"](school)
    except Exception as exc:  # pragma: no cover - defensive for prod DB quirks
        return _base_ctx(
            title="Report error",
            subtitle=str(report_key),
            chart_type="bar",
            labels=[],
            values=[],
            error=f"Could not build this report: {exc}",
        )
    ctx["report_key"] = report_key
    return ctx
