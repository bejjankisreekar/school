from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404
from django.core.exceptions import PermissionDenied
from datetime import date, timedelta
from io import BytesIO

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from apps.customers.models import School
from apps.school_data.models import (
    Student,
    Teacher,
    Attendance,
    Homework,
    Marks,
    Subject,
    ClassRoom,
    Exam,
    Test,
    Section,
    AcademicYear,
    FeeType,
    FeeStructure,
    Fee,
    Payment,
    Parent,
    StudentParent,
    StaffAttendance,
    SupportTicket,
    InventoryItem,
    Purchase,
    Invoice,
    InvoiceItem,
    OnlineAdmission,
    Book,
    BookIssue,
    Hostel,
    HostelRoom,
    HostelAllocation,
    HostelFee,
    Route,
    Vehicle,
    Driver,
    StudentRouteAssignment,
)
User = get_user_model()
from .utils import add_warning_once
from apps.accounts.decorators import (
    admin_required,
    superadmin_required,
    student_required,
    teacher_required,
    parent_required,
    feature_required,
)

# ======================
# Public Pages
# ======================

def home(request):
    return render(request, "marketing/home.html")


def pricing(request):
    return render(request, "marketing/pricing.html")


def about(request):
    return render(request, "marketing/about.html")


def contact(request):
    if request.method == "POST":
        return redirect("core:contact")
    return render(request, "marketing/contact.html")


# ======================
# Super Admin Dashboard
# ======================

@superadmin_required
def super_admin_dashboard(request):
    from django_tenants.utils import tenant_context
    from apps.customers.models import School, Plan
    from apps.school_data.models import Teacher, Student, ClassRoom
    total_schools = School.objects.exclude(schema_name="public").count()
    total_teachers = total_students = total_classes = 0
    for school in School.objects.exclude(schema_name="public"):
        with tenant_context(school):
            total_teachers += Teacher.objects.count()
            total_students += Student.objects.count()
            total_classes += ClassRoom.objects.count()
    plans = Plan.objects.prefetch_related("features").order_by("price_per_student")
    return render(request, "core/dashboards/super_admin_dashboard.html", {
        "total_schools": total_schools,
        "total_teachers": total_teachers,
        "total_students": total_students,
        "total_classes": total_classes,
        "plans": plans,
    })


# ======================
# School Admin Dashboard
# ======================

@admin_required
def admin_dashboard(request):
    from django.db.models.functions import TruncMonth
    from apps.timetable.models import Timetable

    school = request.user.school
    empty_ctx = {
        "current_plan": None,
        "plan_name": "",
        "plan_features": [],
        "trial_expired": False,
        "total_students": 0,
        "total_teachers": 0,
        "total_classes": 0,
        "total_sections": 0,
        "total_subjects": 0,
        "total_tests": 0,
        "active_academic_year": None,
        "active_academic_year_html": "—",
        "attendance_today_count": 0,
        "attendance_present": 0,
        "attendance_absent": 0,
        "recent_homework": [],
        "recent_marks": [],
        "upcoming_exams": [],
        "today_birthdays": [],
        "pending_attendance": [],
        "top_students": [],
        "today_timetable": [],
        "student_growth_data": [],
        "attendance_analytics": {"present": 0, "absent": 0},
        "class_distribution": [],
        "subject_distribution": [],
    }
    if not school:
        return render(request, "core/dashboards/admin_dashboard.html", empty_ctx)
    if school.is_trial_expired():
        return render(request, "core/dashboards/trial_expired.html", {"school": school})

    today = date.today()
    active_academic_year = AcademicYear.objects.filter(is_active=True).first()
    active_academic_year_html = (
        f'<span class="badge bg-success">{active_academic_year.name}</span>'
        if active_academic_year else "—"
    )

    # Statistics
    total_students = Student.objects.count()
    total_teachers = Teacher.objects.count()
    total_classes = ClassRoom.objects.count()
    total_sections = Section.objects.count()
    total_subjects = Subject.objects.count()
    total_tests = Exam.objects.count()

    # Attendance today
    attendance_today_qs = Attendance.objects.filter(date=today)
    attendance_today_count = attendance_today_qs.count()
    attendance_present = attendance_today_qs.filter(status=Attendance.Status.PRESENT).count()
    attendance_absent = attendance_today_qs.filter(status=Attendance.Status.ABSENT).count()

    # Today's timetable (1=Monday, 6=Saturday; Python: Monday=0)
    day_of_week = today.weekday() + 1 if today.weekday() < 6 else None  # Sunday = no classes
    today_timetable = []
    if day_of_week:
        today_timetable = list(
            Timetable.objects.filter(day_of_week=day_of_week)
            .select_related("classroom", "subject", "time_slot")
            .prefetch_related("teachers__user")
            .order_by("time_slot__order", "time_slot__start_time")
        )

    # Upcoming exams (start_date >= today)
    upcoming_exams = list(
        Exam.objects.filter(start_date__gte=today)
        .select_related("classroom")
        .order_by("start_date")[:10]
    )

    # Birthdays today
    today_birthdays = list(
        Student.objects.filter(date_of_birth__month=today.month, date_of_birth__day=today.day)
        .select_related("user", "classroom", "section")
    )

    # Pending attendance: classrooms with students but no attendance record for today
    pending_attendance = list(
        ClassRoom.objects.filter(students__isnull=False)
        .exclude(students__attendance_records__date=today)
        .distinct()
    )

    # Top students by latest marks (percentage)
    top_marks = (
        Marks.objects.filter(total_marks__gt=0)
        .annotate(pct=F("marks_obtained") * 100 / F("total_marks"))
        .select_related("student__user", "student__classroom", "student__section")
        .order_by("-pct")[:10]
    )
    seen_students = set()
    top_students = []
    for m in top_marks:
        if m.student_id not in seen_students:
            seen_students.add(m.student_id)
            pct = round((m.marks_obtained * 100) / m.total_marks)
            top_students.append({"student": m.student, "percentage": pct})
            if len(top_students) >= 5:
                break

    # Chart data: Student growth (last 6 months by enrollment)
    six_months_ago = today - timedelta(days=180)
    student_growth = (
        Student.objects.filter(created_on__gte=six_months_ago)
        .annotate(month=TruncMonth("created_on"))
        .values("month")
        .annotate(count=Count("id"))
        .order_by("month")
    )
    student_growth_data = [{"month": x["month"].strftime("%b %Y"), "count": x["count"]} for x in student_growth]

    # Chart: Attendance analytics (today)
    attendance_analytics = {"present": attendance_present, "absent": attendance_absent}

    # Chart: Class distribution
    class_dist_qs = (
        ClassRoom.objects.annotate(cnt=Count("students"))
        .values("name", "cnt")
        .order_by("name")
    )
    class_distribution = [{"label": x["name"], "count": x["cnt"]} for x in class_dist_qs]

    # Chart: Subject distribution (subjects per class)
    subject_dist_qs = (
        Subject.objects.filter(classroom__isnull=False)
        .values("classroom__name")
        .annotate(cnt=Count("id"))
        .order_by("classroom__name")
    )
    subject_distribution = [{"label": x["classroom__name"], "count": x["cnt"]} for x in subject_dist_qs]

    recent_homework = list(Homework.objects.all().order_by("-id")[:10])
    recent_marks = list(Marks.objects.all().order_by("-id")[:10])

    from apps.customers.subscription import PLAN_FEATURES
    plan = school.plan
    plan_name = (plan.name if plan else "").lower() or "basic"
    plan_features = PLAN_FEATURES.get(plan_name, [])

    return render(request, "core/dashboards/admin_dashboard.html", {
        "total_students": total_students,
        "total_teachers": total_teachers,
        "total_classes": total_classes,
        "total_sections": total_sections,
        "total_subjects": total_subjects,
        "total_tests": total_tests,
        "active_academic_year": active_academic_year,
        "active_academic_year_html": active_academic_year_html,
        "attendance_today_count": attendance_today_count,
        "attendance_present": attendance_present,
        "attendance_absent": attendance_absent,
        "recent_homework": recent_homework,
        "recent_marks": recent_marks,
        "upcoming_exams": upcoming_exams,
        "today_birthdays": today_birthdays,
        "pending_attendance": pending_attendance,
        "top_students": top_students,
        "today_timetable": today_timetable,
        "student_growth_data": student_growth_data,
        "attendance_analytics": attendance_analytics,
        "class_distribution": class_distribution,
        "subject_distribution": subject_distribution,
        "current_plan": plan,
        "plan_name": plan_name,
        "plan_features": plan_features,
        "trial_expired": school.is_trial_expired(),
    })


# ======================
# Teacher Dashboard
# ======================

@teacher_required
def teacher_dashboard(request):
    teacher = getattr(request.user, "teacher_profile", None)
    assigned_subject = None
    if teacher:
        assigned_subject = teacher.subjects.first() or teacher.subject
    today_schedule = []
    try:
        from apps.timetable.views import today_schedule_teacher
        today_schedule = today_schedule_teacher(teacher) if teacher else []
    except Exception:
        pass
    assigned_subject_display = assigned_subject.name if assigned_subject else "Not assigned"
    return render(request, "core/dashboards/teacher_dashboard.html", {
        "assigned_subject": assigned_subject,
        "assigned_subject_display": assigned_subject_display,
        "today_schedule": today_schedule,
    })


# ======================
# Student Dashboard
# ======================

@student_required
def student_dashboard(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        return render(request, "core/student_dashboard/dashboard.html", {
            "attendance_pct": 0,
            "attendance_pct_this_month": 0,
            "latest_exam_pct": None,
            "latest_exam_name": None,
            "total_subjects": 0,
            "overall_pct": 0,
            "attendance_records": [],
            "marks": [],
            "marks_with_pct": [],
            "homework": [],
            "exam_chart_labels": [],
            "exam_chart_data": [],
            "attendance_pie": {"labels": ["Present", "Absent"], "values": [0, 0]},
            "subject_chart_labels": [],
            "subject_chart_data": [],
            "today_classes": [],
        })
    school = request.user.school

    # Attendance percentage (present / (present + absent))
    att_stats = Attendance.objects.filter(student=student).aggregate(
        present=Count("id", filter=Q(status="PRESENT")),
        total=Count("id"),
    )
    total_att = att_stats["total"] or 0
    present_att = att_stats["present"] or 0
    attendance_pct = round((present_att / total_att * 100) if total_att > 0 else 0, 1)

    # Attendance % (This Month)
    today = date.today()
    month_start = today.replace(day=1)
    att_month = Attendance.objects.filter(student=student, date__gte=month_start, date__lte=today).aggregate(
        present=Count("id", filter=Q(status="PRESENT")),
        total=Count("id"),
    )
    total_month = att_month["total"] or 0
    present_month = att_month["present"] or 0
    attendance_pct_this_month = round((present_month / total_month * 100) if total_month > 0 else 0, 1)

    # Total subjects (from marks or school)
    if school:
        total_subjects = Subject.objects.all().count()
    else:
        total_subjects = Marks.objects.filter(student=student).values("subject").distinct().count()

    # Marks with percentage
    marks_qs = Marks.objects.filter(student=student).select_related("subject").order_by("-id")
    marks = list(marks_qs)
    marks_with_pct = [
        {
            "obj": m,
            "pct": round((m.marks_obtained / m.total_marks * 100) if m.total_marks else 0, 1),
        }
        for m in marks
    ]
    # Overall percentage
    if marks:
        total_obtained = sum(m.marks_obtained for m in marks)
        total_max = sum(m.total_marks for m in marks)
        overall_pct = round((total_obtained / total_max * 100) if total_max else 0, 1)
    else:
        overall_pct = 0

    # Latest Exam Percentage + analytics data
    latest_exam_pct = None
    latest_exam_name = None
    marks_by_exam = {}
    for m in Marks.objects.filter(student=student).select_related("subject").order_by("-exam_date", "-id"):
        name = m.exam_name
        if name not in marks_by_exam:
            marks_by_exam[name] = []
        marks_by_exam[name].append(m)
    if marks_by_exam:
        latest_name = max(
            marks_by_exam.keys(),
            key=lambda n: (marks_by_exam[n][0].exam_date or date.min, -marks_by_exam[n][0].id),
        )
        latest_marks = marks_by_exam[latest_name]
        total_o = sum(x.marks_obtained for x in latest_marks)
        total_m = sum(x.total_marks for x in latest_marks)
        latest_exam_pct = round((total_o / total_m * 100) if total_m else 0, 1)
        latest_exam_name = latest_name

    # Analytics: Exam performance (line chart) - ordered by date
    exam_chart_labels = []
    exam_chart_data = []
    sorted_exams = sorted(
        marks_by_exam.items(),
        key=lambda x: (x[1][0].exam_date or date.min, x[1][0].id),
    )
    for exam_name, exam_marks in sorted_exams:
        t_o = sum(m.marks_obtained for m in exam_marks)
        t_m = sum(m.total_marks for m in exam_marks)
        pct = round((t_o / t_m * 100) if t_m else 0, 1)
        exam_chart_labels.append(exam_name)
        exam_chart_data.append(pct)

    # Analytics: Attendance pie (present vs absent)
    att_all = Attendance.objects.filter(student=student).aggregate(
        present=Count("id", filter=Q(status="PRESENT")),
        absent=Count("id", filter=Q(status="ABSENT")),
    )
    attendance_pie = {
        "labels": ["Present", "Absent"],
        "values": [att_all["present"] or 0, att_all["absent"] or 0],
    }

    # Analytics: Subject-wise % for latest exam (bar chart)
    subject_chart_labels = []
    subject_chart_data = []
    if marks_by_exam and latest_exam_name:
        for m in marks_by_exam[latest_exam_name]:
            pct = round((m.marks_obtained / m.total_marks * 100) if m.total_marks else 0, 1)
            subject_chart_labels.append(m.subject.name)
            subject_chart_data.append(pct)

    # Homework from student's school
    if school:
        homework = list(Homework.objects.all().select_related("subject").order_by("due_date")[:20])
    else:
        homework = []

    today_classes = []
    try:
        from apps.timetable.views import today_classes_student
        today_classes = today_classes_student(student)
    except Exception:
        pass
    return render(request, "core/student_dashboard/dashboard.html", {
        "attendance_pct": attendance_pct,
        "attendance_pct_this_month": attendance_pct_this_month,
        "latest_exam_pct": latest_exam_pct,
        "latest_exam_name": latest_exam_name,
        "total_subjects": total_subjects,
        "overall_pct": overall_pct,
        "attendance_records": list(Attendance.objects.filter(student=student).order_by("-date")[:30]),
        "marks": marks,
        "marks_with_pct": marks_with_pct,
        "homework": homework,
        "exam_chart_labels": exam_chart_labels,
        "exam_chart_data": exam_chart_data,
        "attendance_pie": attendance_pie,
        "subject_chart_labels": subject_chart_labels,
        "subject_chart_data": subject_chart_data,
        "today_classes": today_classes,
    })


@student_required
def student_profile(request):
    return render(request, "core/student_dashboard/profile.html")


def _grade_from_pct(pct):
    """90+ A+, 80-89 A, 70-79 B, 60-69 C, 50-59 D, Below 50 F"""
    if pct >= 90:
        return "A+"
    if pct >= 80:
        return "A"
    if pct >= 70:
        return "B"
    if pct >= 60:
        return "C"
    if pct >= 50:
        return "D"
    return "F"


@student_required
def student_attendance(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        return render(request, "core/student/attendance.html", {
            "total_days": 0,
            "present_days": 0,
            "percentage": 0,
            "records": [],
        })
    today = date.today()
    from_date = request.GET.get("from_date", (today.replace(day=1)).isoformat())
    to_date = request.GET.get("to_date", today.isoformat())
    try:
        from_dt = date.fromisoformat(from_date)
        to_dt = date.fromisoformat(to_date)
    except (ValueError, TypeError):
        from_dt = today.replace(day=1)
        to_dt = today
    qs = Attendance.objects.filter(
        student=student,
        date__gte=from_dt,
        date__lte=to_dt,
    ).order_by("-date")
    records = list(qs)
    total_days = len(records)
    present_days = sum(1 for r in records if r.status == "PRESENT")
    percentage = round((present_days / total_days * 100) if total_days > 0 else 0, 2)
    return render(request, "core/student/attendance.html", {
        "from_date": from_date,
        "to_date": to_date,
        "total_days": total_days,
        "present_days": present_days,
        "percentage": percentage,
        "records": records,
    })


def _student_exam_summaries(student):
    """
    Build summary list of exams for a given student.

    Returns list of dicts with keys:
    exam, exam_id, exam_name, exam_date, total_subjects, overall_pct, grade.
    """
    exams = []
    # New Exam-based marks
    exam_marks = Marks.objects.filter(student=student, exam__isnull=False).select_related("exam", "subject")
    by_exam = {}
    for m in exam_marks:
        eid = m.exam_id
        if eid not in by_exam:
            by_exam[eid] = {"exam": m.exam, "marks": []}
        by_exam[eid]["marks"].append(m)
    for eid, data in by_exam.items():
        marks = data["marks"]
        total_o = sum(x.marks_obtained for x in marks)
        total_m = sum(x.total_marks for x in marks)
        pct = round((total_o / total_m * 100) if total_m else 0, 1)
        exams.append({
            "exam": data["exam"],
            "exam_id": eid,
            "exam_name": data["exam"].name,
            "exam_date": data["exam"].start_date,
            "total_subjects": len(marks),
            "overall_pct": pct,
            "grade": _grade_from_pct(pct),
        })

    # Legacy exam_name grouped
    legacy_qs = Marks.objects.filter(student=student, exam__isnull=True).exclude(exam_name="").select_related("subject")
    for m in legacy_qs:
        name = m.exam_name
        if any(e.get("exam_name") == name and e.get("exam") is None for e in exams):
            continue
        group = list(Marks.objects.filter(student=student, exam_name=name, exam__isnull=True))
        total_o = sum(x.marks_obtained for x in group)
        total_m = sum(x.total_marks for x in group)
        pct = round((total_o / total_m * 100) if total_m else 0, 1)
        exams.append({
            "exam": None,
            "exam_id": None,
            "exam_name": name,
            "exam_date": group[0].exam_date if group else None,
            "total_subjects": len(group),
            "overall_pct": pct,
            "grade": _grade_from_pct(pct),
        })
    # Sort by date desc
    exams.sort(key=lambda e: (e["exam_date"] or date.min), reverse=True)
    return exams


@student_required
def student_marks(request):
    return render(request, "core/student_dashboard/marks.html")


@student_required
def student_exams_list(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        return render(request, "core/student/exams_list.html", {"exams": []})
    exams = _student_exam_summaries(student)
    return render(request, "core/student/exams_list.html", {"exams": exams})


@student_required
def student_exam_detail_by_id(request, exam_id):
    """Detail for Exam FK-based marks."""
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    exam = get_object_or_404(Exam, id=exam_id)
    # Student must be in exam's classroom
    if student.classroom_id != exam.classroom_id:
        raise PermissionDenied
    marks_list = list(
        Marks.objects.filter(student=student, exam=exam)
        .select_related("subject")
        .order_by("subject__name")
    )
    if not marks_list:
        raise Http404
    total_obtained = sum(m.marks_obtained for m in marks_list)
    total_max = sum(m.total_marks for m in marks_list)
    overall_pct = round((total_obtained / total_max * 100) if total_max else 0, 1)
    grade = _grade_from_pct(overall_pct)
    rows = [
        {
            "subject": m.subject.name,
            "marks_obtained": m.marks_obtained,
            "total_marks": m.total_marks,
            "pct": round((m.marks_obtained / m.total_marks * 100) if m.total_marks else 0, 1),
            "grade": _grade_from_pct(round((m.marks_obtained / m.total_marks * 100) if m.total_marks else 0, 1)),
        }
        for m in marks_list
    ]
    return render(request, "core/student/exam_detail.html", {
        "exam_name": exam.name,
        "exam_date": exam.start_date,
        "overall_pct": overall_pct,
        "grade": grade,
        "marks_rows": rows,
    })


@student_required
def student_exam_detail(request, exam_name):
    """Detail for legacy exam_name-based marks."""
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    marks_qs = Marks.objects.filter(
        student=student,
        exam_name=exam_name,
        exam__isnull=True,
    ).select_related("subject").order_by("subject__name")
    marks_list = list(marks_qs)
    if not marks_list:
        raise Http404
    total_obtained = sum(m.marks_obtained for m in marks_list)
    total_max = sum(m.total_marks for m in marks_list)
    overall_pct = round((total_obtained / total_max * 100) if total_max else 0, 1)
    grade = _grade_from_pct(overall_pct)
    rows = []
    for m in marks_list:
        pct = round((m.marks_obtained / m.total_marks * 100) if m.total_marks else 0, 1)
        rows.append({
            "subject": m.subject.name,
            "marks_obtained": m.marks_obtained,
            "total_marks": m.total_marks,
            "pct": pct,
            "grade": _grade_from_pct(pct),
        })
    exam_date = marks_list[0].exam_date if marks_list else None
    return render(request, "core/student/exam_detail.html", {
        "exam_name": exam_name,
        "exam_date": exam_date,
        "overall_pct": overall_pct,
        "grade": grade,
        "marks_rows": rows,
    })


@student_required
def student_reports(request):
    """
    Student Reports dashboard: exam summaries, attendance summary, performance summary.
    """
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied

    # Exam summaries
    exams = _student_exam_summaries(student)

    # Attendance summary (all records)
    att_qs = Attendance.objects.filter(student=student).order_by("date")
    total_days = att_qs.count()
    present_days = att_qs.filter(status=Attendance.Status.PRESENT).count()
    attendance_pct = round((present_days / total_days * 100) if total_days else 0, 1)

    # Performance summary from marks
    marks_qs = Marks.objects.filter(student=student, subject__isnull=False).select_related("subject")
    subject_stats = {}
    total_obtained = 0
    total_max = 0
    for m in marks_qs:
        key = m.subject_id
        if key not in subject_stats:
            subject_stats[key] = {"subject": m.subject, "obtained": 0, "max": 0}
        subject_stats[key]["obtained"] += m.marks_obtained
        subject_stats[key]["max"] += m.total_marks
        total_obtained += m.marks_obtained
        total_max += m.total_marks

    strongest = weakest = None
    if subject_stats:
        # Compute percentage per subject
        items = []
        for s in subject_stats.values():
            pct = (s["obtained"] / s["max"] * 100) if s["max"] else 0
            items.append((pct, s["subject"]))
        items.sort(key=lambda x: x[0])
        weakest = items[0]
        strongest = items[-1]

    overall_pct = round((total_obtained / total_max * 100) if total_max else 0, 1)

    # Performance trend based on last two exams
    trend = "N/A"
    recent_exams = [e for e in exams if e.get("overall_pct") is not None]
    if len(recent_exams) >= 2:
        last = recent_exams[0]["overall_pct"]
        prev = recent_exams[1]["overall_pct"]
        if last > prev:
            trend = "Improving"
        elif last < prev:
            trend = "Declining"
        else:
            trend = "Stable"

    context = {
        "exams": exams,
        "attendance": {
            "total_days": total_days,
            "present_days": present_days,
            "percentage": attendance_pct,
        },
        "performance": {
            "strongest_subject": strongest[1].name if strongest else None,
            "strongest_pct": round(strongest[0], 1) if strongest else None,
            "weakest_subject": weakest[1].name if weakest else None,
            "weakest_pct": round(weakest[0], 1) if weakest else None,
            "overall_pct": overall_pct,
            "trend": trend,
        },
    }
    return render(request, "core/student/reports.html", context)


@student_required
def student_report_card_pdf(request, exam_id):
    """
    Generate PDF report card for a specific exam for the logged-in student.
    """
    from .pdf_utils import render_pdf_bytes, pdf_response

    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    exam = get_object_or_404(Exam, id=exam_id)
    # Ensure exam belongs to student's class
    if student.classroom_id != exam.classroom_id:
        raise PermissionDenied

    marks_qs = Marks.objects.filter(student=student, exam=exam).select_related("subject")
    marks_list = list(marks_qs)
    if not marks_list:
        raise Http404

    rows = []
    total_obtained = 0
    total_max = 0
    for m in marks_list:
        pct = round((m.marks_obtained / m.total_marks * 100) if m.total_marks else 0, 1)
        total_obtained += m.marks_obtained
        total_max += m.total_marks
        rows.append({
            "subject": m.subject.name,
            "marks_obtained": m.marks_obtained,
            "total_marks": m.total_marks,
            "pct": pct,
            "grade": _grade_from_pct(pct),
        })
    overall_pct = round((total_obtained / total_max * 100) if total_max else 0, 1)
    overall_grade = _grade_from_pct(overall_pct)

    # Attendance for this student (overall)
    att_qs = Attendance.objects.filter(student=student)
    total_att_days = att_qs.count()
    present_att_days = att_qs.filter(status=Attendance.Status.PRESENT).count()
    attendance_pct = round((present_att_days / total_att_days * 100) if total_att_days else 0, 1)

    school = request.user.school
    academic_year = f"{exam.start_date.year}-{exam.start_date.year + 1}" if exam.start_date else ""

    ai_remarks = ""
    if school and school.has_feature("ai_marksheet_summaries") and rows:
        sorted_rows = sorted(rows, key=lambda r: r["pct"])
        strongest = sorted_rows[-1]
        weakest = sorted_rows[0]
        parts = []
        if strongest["pct"] >= 80:
            parts.append(f"excellent performance in {strongest['subject']}")
        elif strongest["pct"] >= 60:
            parts.append(f"good performance in {strongest['subject']}")
        if weakest["pct"] < 50 and weakest["subject"] != strongest["subject"]:
            parts.append(f"needs improvement in {weakest['subject']}")
        ai_remarks = "Student shows " + " but ".join(parts) + "." if parts else ""

    context = {
        "school": school,
        "student": student,
        "exam": exam,
        "academic_year": academic_year,
        "rows": rows,
        "ai_remarks": ai_remarks,
        "total_obtained": total_obtained,
        "total_max": total_max,
        "overall_pct": overall_pct,
        "overall_grade": overall_grade,
        "attendance_pct": attendance_pct,
        "present_att_days": present_att_days,
        "total_att_days": total_att_days,
    }

    pdf = render_pdf_bytes("core/student/report_card_pdf.html", context)
    if pdf is None:
        return redirect("core:student_reports")
    filename = f"report-card-{exam.name.replace(' ', '-')}.pdf"
    return pdf_response(pdf, filename)


@student_required
def student_attendance_report_pdf(request):
    """
    Generate month-wise attendance PDF for the logged-in student.
    """
    from .pdf_utils import render_pdf_bytes, pdf_response
    from collections import defaultdict

    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied

    att_qs = Attendance.objects.filter(student=student).order_by("date")
    if not att_qs.exists():
        return redirect("core:student_reports")

    monthly = defaultdict(lambda: {"present": 0, "total": 0})
    total_present = 0
    total_days = 0
    for r in att_qs:
        key = r.date.strftime("%Y-%m")
        monthly[key]["total"] += 1
        total_days += 1
        if r.status == Attendance.Status.PRESENT:
            monthly[key]["present"] += 1
            total_present += 1

    monthly_rows = []
    for key in sorted(monthly.keys()):
        year, month = key.split("-")
        from calendar import month_name
        label = f"{month_name[int(month)]} {year}"
        data = monthly[key]
        present = data["present"]
        total = data["total"]
        pct = round((present / total * 100) if total else 0, 1)
        monthly_rows.append({
            "label": label,
            "present": present,
            "absent": total - present,
            "total": total,
            "pct": pct,
        })

    overall_pct = round((total_present / total_days * 100) if total_days else 0, 1)

    school = request.user.school
    context = {
        "school": school,
        "student": student,
        "monthly_rows": monthly_rows,
        "total_present": total_present,
        "total_absent": total_days - total_present,
        "total_days": total_days,
        "overall_pct": overall_pct,
    }
    pdf = render_pdf_bytes("core/student/attendance_report_pdf.html", context)
    if pdf is None:
        return redirect("core:student_reports")
    filename = "attendance-report.pdf"
    return pdf_response(pdf, filename)


# ======================
# School Admin: Student Management
# ======================

@admin_required
@feature_required("students")
def school_students_list(request):
    """List students with filters and pagination."""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    qs = Student.objects.all().select_related("user", "classroom", "section")
    # Filters
    classroom_id = request.GET.get("classroom")
    if classroom_id:
        qs = qs.filter(classroom_id=classroom_id)
    section_id = request.GET.get("section")
    if section_id:
        qs = qs.filter(section_id=section_id)
    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(
            Q(user__first_name__icontains=search) |
            Q(user__last_name__icontains=search) |
            Q(user__username__icontains=search)
        )
    from django.core.paginator import Paginator
    paginator = Paginator(qs.order_by("classroom", "section", "roll_number"), 25)
    page = request.GET.get("page", 1)
    students = paginator.get_page(page)
    classrooms = ClassRoom.objects.select_related("academic_year").order_by("academic_year", "name")
    sections = Section.objects.all().order_by("name")
    return render(request, "core/school/students_list.html", {
        "students": students,
        "classrooms": classrooms,
        "sections": sections,
        "filters": {"classroom_id": classroom_id, "section_id": section_id, "q": search},
    })


@admin_required
def school_student_add(request):
    """Add new student."""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    from .forms import StudentAddForm
    form = StudentAddForm(school, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = User.objects.create_user(
                username=form.cleaned_data["username"],
                password=form.cleaned_data["password"],
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                role=User.Roles.STUDENT,
                school=school,
            )
            student = Student(
                user=user,
                classroom=form.cleaned_data.get("classroom"),
                section=form.cleaned_data.get("section"),
                roll_number=form.cleaned_data["roll_number"],
                admission_number=form.cleaned_data.get("admission_number") or None,
                date_of_birth=form.cleaned_data.get("date_of_birth"),
                parent_name=form.cleaned_data.get("parent_name") or "",
                parent_phone=form.cleaned_data.get("parent_phone") or "",
            )
            student.save_with_audit(request.user)
        return redirect("core:school_students_list")
    return render(request, "core/school/student_add.html", {"form": form})


@admin_required
def school_student_view(request, student_id):
    """View student details (read-only)."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    student = get_object_or_404(Student, id=student_id)
    return render(request, "core/school/student_view.html", {"student": student})


@admin_required
def school_student_edit(request, student_id):
    """Edit student. Only students of logged-in user's school."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    student = get_object_or_404(Student, id=student_id)
    from .forms import StudentEditForm
    initial = {
        "first_name": student.user.first_name,
        "last_name": student.user.last_name,
        "classroom": student.classroom,
        "section": student.section,
        "roll_number": student.roll_number,
        "admission_number": student.admission_number or "",
        "date_of_birth": student.date_of_birth,
        "parent_name": student.parent_name or "",
        "parent_phone": student.parent_phone or "",
    }
    form = StudentEditForm(school, student=student, data=request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            student.user.first_name = form.cleaned_data["first_name"]
            student.user.last_name = form.cleaned_data["last_name"]
            student.user.save()
            student.classroom = form.cleaned_data.get("classroom")
            student.section = form.cleaned_data.get("section")
            student.roll_number = form.cleaned_data["roll_number"]
            student.admission_number = form.cleaned_data.get("admission_number") or None
            student.date_of_birth = form.cleaned_data.get("date_of_birth")
            student.parent_name = form.cleaned_data.get("parent_name") or ""
            student.parent_phone = form.cleaned_data.get("parent_phone") or ""
            student.save_with_audit(request.user)
        return redirect("core:school_students_list")
    return render(request, "core/school/student_edit.html", {"form": form, "student": student})


@admin_required
def school_student_delete(request, student_id):
    """Delete student (full delete: profile + user + related)."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    student = get_object_or_404(Student, id=student_id)
    if request.method != "POST":
        return redirect("core:school_students_list")
    # Placeholder: fees pending check (extend when fee module exists)
    # if has_fees_pending(student):
    #     messages.error(request, "Cannot delete: fees pending.")
    #     return redirect("core:school_students_list")
    with transaction.atomic():
        user = student.user
        student.delete()
        user.delete()
    return redirect("core:school_students_list")


@admin_required
def school_students_import(request):
    """Bulk import students from CSV."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import StudentBulkImportForm
    import csv
    import io
    form = StudentBulkImportForm(request.POST or None, request.FILES or None)
    errors = []
    created = 0
    if request.method == "POST" and form.is_valid():
        f = request.FILES["csv_file"]
        try:
            content = f.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content))
            required = ["name", "username", "password", "class", "section", "roll_number"]
            with transaction.atomic():
                for i, row in enumerate(reader, start=2):
                    try:
                        name = (row.get("name") or "").strip()
                        username = (row.get("username") or "").strip()
                        password = (row.get("password") or "").strip()
                        cls = (row.get("class") or "").strip()
                        sec = (row.get("section") or "").strip()
                        roll = (row.get("roll_number") or "").strip()
                        if not all([name, username, password, cls, roll]):
                            errors.append(f"Row {i}: Missing required fields")
                            continue
                        classroom = ClassRoom.objects.filter(name=cls).first()
                        section = Section.objects.filter(name__iexact=sec).first() if sec else None
                        if section and classroom and section not in classroom.sections.all():
                            section = None
                        if User.objects.filter(username=username).exists():
                            errors.append(f"Row {i}: Username {username} exists")
                            continue
                        parts = name.split(None, 1)
                        first_name = parts[0] if parts else username
                        last_name = parts[1] if len(parts) > 1 else ""
                        user = User.objects.create_user(
                            username=username, password=password,
                            first_name=first_name, last_name=last_name,
                            role=User.Roles.STUDENT, school=school,
                        )
                        student = Student(
                            user=user, classroom=classroom, section=section,
                            roll_number=roll,
                        )
                        student.save_with_audit(request.user)
                        created += 1
                    except Exception as e:
                        errors.append(f"Row {i}: {e}")
        except Exception:
            pass
        return redirect("core:school_students_list")
    return render(request, "core/school/students_import.html", {"form": form})


# ======================
# School Admin: Teacher Management
# ======================

@admin_required
@feature_required("teachers")
def school_teachers_list(request):
    """List teachers with actions."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    teachers = Teacher.objects.all().select_related("user", "user__school").prefetch_related("subjects", "classrooms")
    return render(request, "core/school/teachers_list.html", {"teachers": teachers})


@admin_required
def school_teacher_add(request):
    """Add new teacher."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import TeacherAddForm
    form = TeacherAddForm(school, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = User.objects.create_user(
                username=form.cleaned_data["username"],
                password=form.cleaned_data["password"],
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                role=User.Roles.TEACHER,
                school=school,
            )
            teacher = Teacher(
                user=user,
                employee_id=form.cleaned_data.get("employee_id") or "",
                phone_number=form.cleaned_data.get("phone_number") or "",
            )
            teacher.save_with_audit(request.user)
            teacher.subjects.set(form.cleaned_data.get("subjects") or [])
            teacher.classrooms.set(form.cleaned_data.get("classrooms") or [])
        return redirect("core:school_teachers_list")
    return render(request, "core/school/teacher_add.html", {"form": form})


@admin_required
def school_teacher_view(request, teacher_id):
    """View teacher details (read-only)."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    teacher = get_object_or_404(Teacher, id=teacher_id)
    return render(request, "core/school/teacher_view.html", {"teacher": teacher})


@admin_required
def school_teacher_edit(request, teacher_id):
    """Edit teacher. Only teachers of logged-in user's school."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    teacher = get_object_or_404(Teacher, id=teacher_id)
    from .forms import TeacherEditForm
    initial = {
        "first_name": teacher.user.first_name,
        "last_name": teacher.user.last_name,
        "email": teacher.user.email or "",
        "phone_number": teacher.phone_number or "",
        "qualification": teacher.qualification or "",
        "experience": teacher.experience or "",
        "role": teacher.user.role,
        "subjects": list(teacher.subjects.all()),
        "classrooms": list(teacher.classrooms.all()),
    }
    form = TeacherEditForm(school, teacher=teacher, data=request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            teacher.user.first_name = form.cleaned_data["first_name"]
            teacher.user.last_name = form.cleaned_data["last_name"]
            teacher.user.email = form.cleaned_data.get("email") or ""
            teacher.user.role = form.cleaned_data["role"]
            teacher.user.save()
            teacher.phone_number = form.cleaned_data.get("phone_number") or ""
            teacher.qualification = form.cleaned_data.get("qualification") or ""
            teacher.experience = form.cleaned_data.get("experience") or ""
            teacher.save_with_audit(request.user)
            teacher.subjects.set(form.cleaned_data.get("subjects") or [])
            teacher.classrooms.set(form.cleaned_data.get("classrooms") or [])
        return redirect("core:school_teachers_list")
    return render(request, "core/school/teacher_edit.html", {"form": form, "teacher": teacher})


@admin_required
def school_teacher_delete(request, teacher_id):
    """Delete teacher (block if in timetable)."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    teacher = get_object_or_404(Teacher, id=teacher_id)
    if request.method != "POST":
        return redirect("core:school_teachers_list")
    from apps.timetable.models import Timetable
    if Timetable.objects.filter(teachers=teacher).exists():
        return redirect("core:school_teachers_list")
    with transaction.atomic():
        user = teacher.user
        teacher.delete()
        user.delete()
    return redirect("core:school_teachers_list")


# ======================
# School Admin: Section Management
# ======================

@admin_required
@feature_required("students")
def school_sections(request):
    """List Sections with pagination, search, filter."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from django.core.paginator import Paginator

    qs = Section.objects.prefetch_related("classrooms").annotate(student_count=Count("students")).order_by("name")
    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))
    paginator = Paginator(qs, 15)
    page = request.GET.get("page", 1)
    sections = paginator.get_page(page)
    return render(request, "core/school/sections.html", {
        "sections": sections,
        "filters": {"q": search},
    })


@admin_required
def school_section_add(request):
    """Add new section form page."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import SectionForm

    form = SectionForm(school, request.POST or None)
    if request.method == "POST" and form.is_valid():
        section = form.save(commit=False)
        section.save_with_audit(request.user)
        return redirect("core:school_sections")
    return render(request, "core/school/section_add.html", {"form": form, "title": "Add Section"})


@admin_required
def school_section_edit(request, section_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    section = get_object_or_404(Section, id=section_id)
    from .forms import SectionForm
    form = SectionForm(school, request.POST or None, instance=section)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.modified_by = request.user
        obj.save()
        return redirect("core:school_sections")
    return render(request, "core/school/section_edit.html", {"form": form, "section": section})


@admin_required
def school_section_delete(request, section_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    section = get_object_or_404(Section, id=section_id)
    if request.method != "POST":
        return redirect("core:school_sections")
    if section.students.exists():
        return redirect("core:school_sections")
    section.delete()
    return redirect("core:school_sections")


# ======================
# School Admin: Academic Years
# ======================

@admin_required
def school_academic_years(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import AcademicYearForm
    from django.core.paginator import Paginator

    form = AcademicYearForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.save_with_audit(request.user)
        return redirect("core:school_academic_years")

    qs = AcademicYear.objects.all().order_by("-start_date")
    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(name__icontains=search)
    paginator = Paginator(qs, 15)
    page = request.GET.get("page", 1)
    academic_years = paginator.get_page(page)
    return render(request, "core/school/academic_year/list.html", {
        "form": form,
        "academic_years": academic_years,
        "filters": {"q": search},
    })


@admin_required
def school_academic_year_set_active(request, year_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    if request.method != "POST":
        return redirect("core:school_academic_years")
    ay = get_object_or_404(AcademicYear, id=year_id)
    ay.is_active = True
    ay.modified_by = request.user
    ay.save(update_fields=["is_active", "modified_by", "modified_on"])
    return redirect("core:school_academic_years")


@admin_required
def school_academic_year_edit(request, year_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ay = get_object_or_404(AcademicYear, id=year_id)
    from .forms import AcademicYearForm
    form = AcademicYearForm(request.POST or None, instance=ay)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.modified_by = request.user
        obj.save()
        return redirect("core:school_academic_years")
    return render(request, "core/school/academic_year/form.html", {"form": form, "academic_year": ay, "title": "Edit Academic Year"})


@admin_required
def school_academic_year_delete(request, year_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    if request.method != "POST":
        return redirect("core:school_academic_years")
    ay = get_object_or_404(AcademicYear, id=year_id)
    if ay.is_active:
        return redirect("core:school_academic_years")
    ay.delete()
    return redirect("core:school_academic_years")


# ======================
# School Admin: Classes (Grade levels)
# ======================

@admin_required
@feature_required("students")
def school_classes(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from django.core.paginator import Paginator

    qs = ClassRoom.objects.all().select_related("academic_year").annotate(
        section_count=Count("sections", distinct=True),
        student_count=Count("students", distinct=True),
    ).order_by("academic_year", "name")
    academic_year_id = request.GET.get("academic_year")
    if academic_year_id:
        qs = qs.filter(academic_year_id=academic_year_id)
    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(name__icontains=search)
    paginator = Paginator(qs, 15)
    page = request.GET.get("page", 1)
    classes = paginator.get_page(page)
    academic_years = AcademicYear.objects.all().order_by("-start_date")
    return render(request, "core/school/classes/list.html", {
        "classes": classes,
        "academic_years": academic_years,
        "filters": {"academic_year": academic_year_id, "q": search},
    })


@admin_required
def school_class_add(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import ClassRoomForm
    form = ClassRoomForm(school, request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.save_with_audit(request.user)
        form.save_m2m()
        return redirect("core:school_classes")
    return render(request, "core/school/classes/form.html", {"form": form, "title": "Add Class"})


@admin_required
def school_class_edit(request, class_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    classroom = get_object_or_404(ClassRoom, id=class_id)
    from .forms import ClassRoomForm
    form = ClassRoomForm(school, request.POST or None, instance=classroom)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.modified_by = request.user
        obj.save()
        form.save_m2m()
        return redirect("core:school_classes")
    return render(request, "core/school/classes/form.html", {"form": form, "classroom": classroom, "title": "Edit Class"})


@admin_required
def school_class_delete(request, class_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    if request.method != "POST":
        return redirect("core:school_classes")
    classroom = get_object_or_404(ClassRoom, id=class_id)
    if classroom.sections.exists():
        return redirect("core:school_classes")
    if classroom.students.exists():
        return redirect("core:school_classes")
    classroom.delete()
    return redirect("core:school_classes")


# ======================
# School Admin: Subjects
# ======================

@admin_required
@feature_required("students")
def school_subjects(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from django.core.paginator import Paginator

    qs = Subject.objects.all().select_related("classroom", "classroom__academic_year", "teacher", "teacher__user", "academic_year").order_by("academic_year", "classroom__name", "name")
    academic_year_id = request.GET.get("academic_year")
    if academic_year_id:
        qs = qs.filter(academic_year_id=academic_year_id)
    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(code__icontains=search))
    paginator = Paginator(qs, 15)
    page = request.GET.get("page", 1)
    subjects = paginator.get_page(page)
    academic_years = AcademicYear.objects.all().order_by("-start_date")
    return render(request, "core/school/subjects/list.html", {
        "subjects": subjects,
        "academic_years": academic_years,
        "filters": {"academic_year": academic_year_id, "q": search},
    })


@admin_required
def school_subject_add(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import SubjectForm
    form = SubjectForm(school, request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.save_with_audit(request.user)
        return redirect("core:school_subjects")
    return render(request, "core/school/subjects/form.html", {"form": form, "title": "Add Subject"})


@admin_required
def school_subject_edit(request, subject_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    subject = get_object_or_404(Subject, id=subject_id)
    from .forms import SubjectForm
    form = SubjectForm(school, request.POST or None, instance=subject)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.modified_by = request.user
        obj.save()
        return redirect("core:school_subjects")
    return render(request, "core/school/subjects/form.html", {"form": form, "subject": subject, "title": "Edit Subject"})


@admin_required
def school_subject_delete(request, subject_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    if request.method != "POST":
        return redirect("core:school_subjects")
    subject = get_object_or_404(Subject, id=subject_id)
    subject.delete()
    return redirect("core:school_subjects")


# ======================
# School Admin: Tests Management
# ======================


@admin_required
@feature_required("exams")
def school_tests_list(request):
    """List all tests with filters."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")

    qs = Test.objects.select_related("subject", "classroom", "section").order_by("-test_date", "classroom__name", "subject__name")

    class_filter = request.GET.get("class")
    subject_filter = request.GET.get("subject")
    date_filter = request.GET.get("date")

    if class_filter:
        qs = qs.filter(classroom_id=class_filter)
    if subject_filter:
        qs = qs.filter(subject_id=subject_filter)
    if date_filter:
        qs = qs.filter(test_date=date_filter)

    classrooms = ClassRoom.objects.order_by("academic_year", "name")
    subjects = Subject.objects.order_by("name")

    return render(request, "core/school/tests_list.html", {
        "tests": qs,
        "classrooms": classrooms,
        "subjects": subjects,
        "filters": {
            "class": class_filter,
            "subject": subject_filter,
            "date": date_filter,
        },
    })


@admin_required
def school_test_add(request):
    """Add a new test."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import TestForm
    form = TestForm(school, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("core:school_tests_list")
    return render(request, "core/school/test_form.html", {"form": form, "title": "Add Test"})


@admin_required
def school_test_edit(request, test_id):
    """Edit a test."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    test = get_object_or_404(Test, id=test_id)
    from .forms import TestForm
    form = TestForm(school, request.POST or None, instance=test)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("core:school_tests_list")
    return render(request, "core/school/test_form.html", {"form": form, "test": test, "title": "Edit Test"})


@admin_required
def school_test_delete(request, test_id):
    """Delete a test."""
    if not request.user.school:
        return redirect("core:admin_dashboard")
    test = get_object_or_404(Test, id=test_id)
    if request.method == "POST":
        test.delete()
    return redirect("core:school_tests_list")


# ======================
# Placeholder / Coming Soon
# ======================

@login_required
def students_list(request):
    return render(request, "core/placeholders/coming_soon.html", {"title": "Students"})

@login_required
def teachers_list(request):
    return render(request, "core/placeholders/coming_soon.html", {"title": "Teachers"})

@login_required
def attendance_list(request):
    return render(request, "core/placeholders/coming_soon.html", {"title": "Attendance"})

@login_required
def marks_list(request):
    return render(request, "core/placeholders/coming_soon.html", {"title": "Marks"})

@login_required
def homework_list(request):
    return render(request, "core/placeholders/coming_soon.html", {"title": "Homework"})

@login_required
def reports_list(request):
    """Legacy reports entry (student/general)."""
    return render(request, "core/placeholders/coming_soon.html", {"title": "Reports"})


@admin_required
def school_reports_dashboard(request):
    """
    School Reports Dashboard: charts + report cards.
    Uses Marks aggregates for analytics.
    """
    from apps.school_data.models import Marks
    from django.db.models import Sum
    import json

    qs = Marks.objects.filter(exam__isnull=False)

    # Top 10 students by percentage
    agg = qs.values("student_id").annotate(
        total_o=Sum("marks_obtained"), total_m=Sum("total_marks")
    )
    top_students = []
    for row in agg:
        if row["total_m"]:
            pct = round(row["total_o"] / row["total_m"] * 100, 1)
            top_students.append((row["student_id"], pct))
    top_students.sort(key=lambda x: -x[1])
    top_students = top_students[:10]
    student_map = {
        s.id: s
        for s in Student.objects.filter(id__in=[sid for sid, _ in top_students]).select_related(
            "user", "classroom"
        )
    }
    chart_top_labels = [
        (student_map[sid].user.get_full_name() or student_map[sid].user.username)
        if sid in student_map
        else str(sid)
        for sid, _ in top_students
    ]
    chart_top_values = [pct for _, pct in top_students]

    # Grade distribution (simple A/B/C/D buckets) based on top students
    grade_buckets = {"A": 0, "B": 0, "C": 0, "D": 0}
    for _, pct in top_students:
        if pct >= 80:
            grade_buckets["A"] += 1
        elif pct >= 65:
            grade_buckets["B"] += 1
        elif pct >= 50:
            grade_buckets["C"] += 1
        else:
            grade_buckets["D"] += 1

    # Class-wise average percentage
    class_agg = (
        qs.values("student__classroom__name")
        .annotate(total_o=Sum("marks_obtained"), total_m=Sum("total_marks"))
        .order_by("student__classroom__name")
    )
    class_labels = []
    class_values = []
    for row in class_agg:
        cname = row["student__classroom__name"] or "Unassigned"
        if row["total_m"]:
            pct = round(row["total_o"] / row["total_m"] * 100, 1)
            class_labels.append(cname)
            class_values.append(pct)

    # Performance trend over exams (average percentage per exam)
    exam_agg = (
        qs.values("exam__name")
        .annotate(total_o=Sum("marks_obtained"), total_m=Sum("total_marks"))
        .order_by("exam__name")
    )
    trend_labels = []
    trend_values = []
    for row in exam_agg:
        if row["total_m"]:
            pct = round(row["total_o"] / row["total_m"] * 100, 1)
            trend_labels.append(row["exam__name"] or "Exam")
            trend_values.append(pct)

    context = {
        "chart_top_labels": json.dumps(chart_top_labels),
        "chart_top_values": json.dumps(chart_top_values),
        "grade_labels": json.dumps(list(grade_buckets.keys())),
        "grade_values": json.dumps(list(grade_buckets.values())),
        "class_labels": json.dumps(class_labels),
        "class_values": json.dumps(class_values),
        "trend_labels": json.dumps(trend_labels),
        "trend_values": json.dumps(trend_values),
    }
    return render(request, "core/reports/dashboard.html", context)


# ======================
# Teacher Actions
# ======================

@teacher_required
def teacher_students_list(request):
    school = request.user.school
    if not school:
        students = []
    else:
        students = Student.objects.all().select_related("user")
    return render(request, "core/teacher/students_list.html", {"students": students})


@teacher_required
def create_homework(request):
    from .forms import TeacherHomeworkForm

    teacher = getattr(request.user, "teacher_profile", None)
    subject = None
    if teacher:
        subject = teacher.subjects.first() or teacher.subject
    if not teacher or not subject:
        return redirect("core:teacher_dashboard")

    if request.method == "POST":
        form = TeacherHomeworkForm(request.POST)
        if form.is_valid():
            hw = form.save(commit=False)
            hw.teacher = teacher
            hw.subject = subject
            hw.save()
            return redirect("core:teacher_dashboard")
    else:
        form = TeacherHomeworkForm()

    return render(request, "core/teacher/homework_form.html", {"form": form, "title": "Create Homework", "subject": subject})


@teacher_required
def enter_marks(request):
    from .forms import MarksForm

    teacher = getattr(request.user, "teacher_profile", None)
    school = request.user.school
    if not teacher or not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")

    if request.method == "POST":
        form = MarksForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("core:teacher_dashboard")
    else:
        form = MarksForm()
        form.fields["student"].queryset = Student.objects.all()
        from apps.school_data.models import Subject
        if teacher.subjects.exists():
            form.fields["subject"].queryset = Subject.objects.filter(id__in=teacher.subjects.values_list("id", flat=True))
        elif teacher.subject:
            form.fields["subject"].queryset = Subject.objects.filter(id=teacher.subject_id)
        else:
            form.fields["subject"].queryset = Subject.objects.all()

    return render(request, "core/teacher/marks_form.html", {"form": form, "title": "Enter Marks"})


def _teacher_exam_access(exam, school):
    """Ensure teacher's school can access this exam."""
    return exam is not None


@teacher_required
@feature_required("exams")
def teacher_exams(request):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")
    # New Exam model list (teacher's school)
    exam_objs = list(
        Exam.objects.all()
        .select_related("classroom")
        .order_by("-start_date")
    )
    return render(request, "core/teacher/exams.html", {
        "exams": exam_objs,
    })


@teacher_required
def teacher_exam_create(request):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")
    if request.method == "POST":
        from .forms import ExamCreateForm
        form = ExamCreateForm(request.POST)
        form.fields["classroom"].queryset = ClassRoom.objects.all()
        if form.is_valid():
            exam = form.save(commit=False)
            exam.save()
            return redirect("core:teacher_exam_summary", exam_id=exam.id)
    else:
        from .forms import ExamCreateForm
        form = ExamCreateForm()
        form.fields["classroom"].queryset = ClassRoom.objects.all()
    return render(request, "core/teacher/exam_create.html", {"form": form})


@teacher_required
def teacher_exam_summary(request, exam_id):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")
    exam = get_object_or_404(Exam, id=exam_id)
    if not _teacher_exam_access(exam, school):
        raise PermissionDenied
    # Get students in exam's classroom (all sections)
    students = list(
        Student.objects.filter(
            classroom=exam.classroom,
            user__school=school,
        )
        .select_related("user")
        .order_by("roll_number")
    )
    # Per-student totals from Marks for this exam
    marks_qs = Marks.objects.filter(exam=exam).select_related("student", "subject")
    student_totals = {}
    for m in marks_qs:
        sid = m.student_id
        if sid not in student_totals:
            student_totals[sid] = {"obtained": 0, "total": 0}
        student_totals[sid]["obtained"] += m.marks_obtained
        student_totals[sid]["total"] += m.total_marks
    rows = []
    all_pcts = []
    for s in students:
        t = student_totals.get(s.id, {"obtained": 0, "total": 0})
        tot_max = t["total"] or 1
        pct = round((t["obtained"] / tot_max) * 100, 1)
        all_pcts.append(pct)
        rows.append({
            "student": s,
            "name": s.user.get_full_name() or s.user.username,
            "total_obtained": t["obtained"],
            "total_marks": t["total"],
            "percentage": pct,
            "grade": _grade_from_pct(pct),
        })
    class_avg = round(sum(all_pcts) / len(all_pcts), 1) if all_pcts else 0
    return render(request, "core/teacher/exam_summary.html", {
        "exam": exam,
        "rows": rows,
        "class_avg": class_avg,
    })


@teacher_required
def teacher_exam_enter_marks(request, exam_id):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")
    exam = get_object_or_404(Exam, id=exam_id)
    if not _teacher_exam_access(exam, school):
        raise PermissionDenied

    # Subjects: classroom-specific or school-level
    subjects = Subject.objects.filter(
        Q(classroom=exam.classroom) | Q(classroom__isnull=True),
        school=school,
    ).order_by("name")

    # Students in exam's classroom (all sections)
    students = list(
        Student.objects.filter(
            classroom=exam.classroom,
            user__school=school,
        )
        .select_related("user")
        .order_by("roll_number")
    )

    subject_id = request.GET.get("subject") or request.POST.get("subject")
    subject = None
    if subject_id:
        subject = subjects.filter(id=subject_id).first()

    if request.method == "POST" and subject:
        with transaction.atomic():
            existing = {
                (m.student_id, m.subject_id): m
                for m in Marks.objects.filter(exam=exam, subject=subject)
            }
            to_create = []
            to_update = []
            for s in students:
                try:
                    obtained = int(request.POST.get(f"obtained_{s.id}", 0) or 0)
                    total = int(request.POST.get(f"total_{s.id}", 100) or 100)
                except (ValueError, TypeError):
                    obtained = 0
                    total = 100
                key = (s.id, subject.id)
                if key in existing:
                    rec = existing[key]
                    if rec.marks_obtained != obtained or rec.total_marks != total:
                        rec.marks_obtained = obtained
                        rec.total_marks = total
                        rec.entered_by = request.user
                        to_update.append(rec)
                else:
                    to_create.append(
                        Marks(
                            student=s,
                            subject=subject,
                            exam=exam,
                            marks_obtained=obtained,
                            total_marks=total,
                            entered_by=request.user,
                        )
                    )
            if to_create:
                Marks.objects.bulk_create(to_create)
            if to_update:
                Marks.objects.bulk_update(to_update, ["marks_obtained", "total_marks", "entered_by"])
        return redirect("core:teacher_exam_summary", exam_id=exam.id)

    # GET: show form
    existing_marks = {}
    if subject:
        for m in Marks.objects.filter(exam=exam, subject=subject):
            existing_marks[m.student_id] = {"obtained": m.marks_obtained, "total": m.total_marks}

    students_with_marks = []
    for s in students:
        em = existing_marks.get(s.id, {"obtained": 0, "total": 100})
        students_with_marks.append({
            "student": s,
            "obtained": em["obtained"],
            "total": em["total"],
        })

    return render(request, "core/teacher/exam_enter_marks.html", {
        "exam": exam,
        "subjects": subjects,
        "subject": subject,
        "students_with_marks": students_with_marks,
    })


@teacher_required
def teacher_class_analytics(request):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")

    students = list(Student.objects.all().select_related("user"))

    # Overall % per student from Marks
    student_pcts = []
    for s in students:
        marks_qs = Marks.objects.filter(student=s).aggregate(
            total_obtained=Sum("marks_obtained"),
            total_max=Sum("total_marks"),
        )
        total_o = marks_qs["total_obtained"] or 0
        total_m = marks_qs["total_max"] or 0
        pct = round((total_o / total_m * 100) if total_m else 0, 1)
        student_pcts.append({
            "student": s,
            "name": s.user.get_full_name() or s.user.username,
            "pct": pct,
        })

    # Sort by pct descending
    sorted_by_pct = sorted(student_pcts, key=lambda x: x["pct"], reverse=True)
    top_5 = sorted_by_pct[:5]
    bottom_5 = sorted_by_pct[-5:][::-1] if len(sorted_by_pct) >= 5 else list(reversed(sorted_by_pct))

    # Class average
    class_avg = round(sum(x["pct"] for x in student_pcts) / len(student_pcts), 1) if student_pcts else 0

    # Subject-wise class average
    subjects = Subject.objects.all()
    subject_avgs = []
    for subj in subjects:
        agg = Marks.objects.filter(subject=subj).aggregate(
            total_o=Sum("marks_obtained"),
            total_m=Sum("total_marks"),
        )
        t_o = agg["total_o"] or 0
        t_m = agg["total_m"] or 0
        avg = round((t_o / t_m * 100) if t_m else 0, 1)
        subject_avgs.append({"name": subj.name, "avg": avg})
    subject_chart_labels = [s["name"] for s in subject_avgs]
    subject_chart_data = [s["avg"] for s in subject_avgs]

    # Attendance comparison: per-student attendance %
    att_chart_labels = []
    att_chart_data = []
    for item in sorted_by_pct[:15]:
        s = item["student"]
        att = Attendance.objects.filter(student=s).aggregate(
            present=Count("id", filter=Q(status="PRESENT")),
            total=Count("id"),
        )
        total = att["total"] or 0
        present = att["present"] or 0
        pct = round((present / total * 100) if total else 0, 1)
        att_chart_labels.append(item["name"][:12] + ("…" if len(item["name"]) > 12 else ""))
        att_chart_data.append(pct)

    return render(request, "core/teacher/class_analytics.html", {
        "top_5": top_5,
        "bottom_5": bottom_5,
        "class_avg": class_avg,
        "ranking": sorted_by_pct,
        "subject_chart_labels": subject_chart_labels,
        "subject_chart_data": subject_chart_data,
        "att_chart_labels": att_chart_labels,
        "att_chart_data": att_chart_data,
    })


def _get_class_section_choices(school):
    """Get unique (class_name, section_name) from students and classrooms."""
    choices_class = set()
    choices_section = set()
    # From students: classroom name + section name
    for row in Student.objects.filter(
        classroom__isnull=False, section__isnull=False
    ).values_list("classroom__name", "section__name"):
        if row[0] and row[1]:
            choices_class.add((row[0], row[0]))
            choices_section.add((row[1], row[1]))
    # From classrooms: name + each section in that class
    for c in ClassRoom.objects.prefetch_related("sections"):
        choices_class.add((c.name, c.name))
        for sec in c.sections.all():
            choices_section.add((sec.name, sec.name))
    return sorted(choices_class), sorted(choices_section)


@teacher_required
@feature_required("attendance")
def bulk_attendance(request):
    """Bulk attendance by class-section. URL: /teacher/attendance/"""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")

    today = date.today()
    class_choices, section_choices = _get_class_section_choices(school)

    # POST: Save attendance
    if request.method == "POST":
        class_name = request.POST.get("class_name", "").strip()
        section_val = request.POST.get("section", "").strip()
        date_str = request.POST.get("attendance_date", "")
        if not class_name or not section_val or not date_str:
            return redirect("core:bulk_attendance")
        try:
            att_date = date.fromisoformat(date_str)
        except (ValueError, TypeError):
            return redirect("core:bulk_attendance")
        if att_date > today:
            return redirect("core:bulk_attendance")

        # Get students (by classroom or grade+section)
        students = list(
            Student.objects.all()
            .filter(
                Q(classroom__name=class_name, classroom__section=section_val)
                | Q(classroom__isnull=True, grade=class_name, section=section_val)
            )
            .select_related("user")
            .order_by("roll_number")
        )
        if not students:
            return redirect("core:bulk_attendance")

        # Collect attendance updates
        existing = {
            a.student_id: a
            for a in Attendance.objects.filter(
                student__in=students,
                date=att_date,
            )
        }
        to_create = []
        to_update = []
        for s in students:
            status = request.POST.get(f"status_{s.id}", "PRESENT")
            if status not in ("PRESENT", "ABSENT"):
                status = "PRESENT"
            if s.id in existing:
                rec = existing[s.id]
                if rec.status != status:
                    rec.status = status
                    rec.marked_by = request.user
                    to_update.append(rec)
            else:
                to_create.append(
                    Attendance(
                        student=s,
                        date=att_date,
                        status=status,
                        marked_by=request.user,
                    )
                )
        if to_create:
            Attendance.objects.bulk_create(to_create)
        if to_update:
            Attendance.objects.bulk_update(to_update, ["status", "marked_by"])
        return redirect("core:bulk_attendance")

    # GET: Load students or show form
    class_name = request.GET.get("class_name", "").strip()
    section_val = request.GET.get("section", "").strip()
    date_str = request.GET.get("attendance_date", today.isoformat())
    try:
        att_date = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        att_date = today
    future_date = att_date > today

    students = []
    existing_attendance = {}
    if class_name and section_val:
        students = list(
            Student.objects.filter(
                classroom__name=class_name,
                section__name__iexact=section_val,
            )
            .select_related("user")
            .order_by("roll_number")
        )
        if students:
            att_map = {
                a.student_id: a.status
                for a in Attendance.objects.filter(
                    student__in=students,
                    date=att_date,
                )
            }
        students_with_status = [
            {"student": s, "status": att_map.get(s.id, "PRESENT")}
            for s in students
        ]
    else:
        students_with_status = []

    return render(request, "core/teacher/bulk_attendance.html", {
        "class_choices": class_choices,
        "section_choices": section_choices,
        "class_name": class_name,
        "section_val": section_val,
        "attendance_date": date_str,
        "students_with_status": students_with_status,
        "future_date": future_date,
    })


@teacher_required
@feature_required("attendance")
def mark_attendance(request):
    """Legacy single-student attendance form."""
    from .forms import AttendanceForm

    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")

    if request.method == "POST":
        form = AttendanceForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("core:teacher_dashboard")
    else:
        form = AttendanceForm(initial={"date": date.today()})
        form.fields["student"].queryset = Student.objects.all()

    return render(request, "core/teacher/attendance_form.html", {"form": form, "title": "Mark Attendance"})


# ======================
# Fee & Billing (Basic Plan)
# ======================


def _school_fee_check(request):
    """Ensure school has fee module. Return school or None."""
    return _school_module_check(request, "fees")


def _school_module_check(request, feature: str):
    """Ensure school has access to feature. Return school or None."""
    school = request.user.school
    if not school:
        return None
    if school.is_trial_expired():
        return None
    if not school.has_feature(feature):
        return None
    return school


@admin_required
def school_fees_index(request):
    """Fee management index: structure, dues, collections."""
    school = _school_fee_check(request)
    if not school:
        add_warning_once(request, "fee_not_available_shown", "Fee module not available.")
        return redirect("core:admin_dashboard")
    fee_types = FeeType.objects.all()
    structures = FeeStructure.objects.all().select_related("fee_type", "classroom")
    dues = Fee.objects.all().select_related("student", "fee_structure").order_by("-due_date")[:20]
    return render(request, "core/fees/index.html", {
        "fee_types": fee_types,
        "structures": structures,
        "dues": dues,
    })


@admin_required
def school_fee_types(request):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import FeeTypeForm
    items = FeeType.objects.all()
    if request.method == "POST":
        form = FeeTypeForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.save_with_audit(request.user)
            return redirect("core:school_fee_types")
    else:
        form = FeeTypeForm()
    return render(request, "core/fees/fee_types.html", {"form": form, "items": items})


@admin_required
def school_fee_structure(request):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import FeeStructureForm
    items = FeeStructure.objects.all().select_related("fee_type", "classroom", "academic_year")
    if request.method == "POST":
        form = FeeStructureForm(school, request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.save_with_audit(request.user)
            return redirect("core:school_fee_structure")
    else:
        form = FeeStructureForm(school)
    return render(request, "core/fees/fee_structure.html", {"form": form, "items": items})


@admin_required
def school_fee_add(request):
    """Create fee dues for students from fee structure."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    if request.method == "POST":
        structure_id = request.POST.get("fee_structure")
        classroom_id = request.POST.get("classroom")
        due_date_str = request.POST.get("due_date")
        if structure_id and due_date_str:
            try:
                structure = FeeStructure.objects.get(id=structure_id)
                due_date = date.fromisoformat(due_date_str)
                classroom = ClassRoom.objects.filter(id=classroom_id).first() if classroom_id else None
                students = Student.objects.all()
                if classroom:
                    students = students.filter(classroom=classroom)
                created = 0
                for s in students:
                    _, created_flag = Fee.objects.get_or_create(
                        student=s,
                        fee_structure=structure,
                        due_date=due_date,
                        defaults={"amount": structure.amount},
                    )
                    if created_flag:
                        created += 1
            except (ValueError, FeeStructure.DoesNotExist):
                pass
        return redirect("core:school_fee_collection")
    structures = FeeStructure.objects.all().select_related("fee_type", "classroom")
    classrooms = ClassRoom.objects.all()
    return render(request, "core/fees/fee_add.html", {"structures": structures, "classrooms": classrooms})


@admin_required
def school_fee_collection(request):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    dues = Fee.objects.filter( status__in=["PENDING", "PARTIAL"]).select_related(
        "student__user", "fee_structure__fee_type"
    ).order_by("due_date")
    return render(request, "core/fees/fee_collection.html", {"dues": dues})


@admin_required
def school_fee_collect(request, fee_id):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    fee = get_object_or_404(Fee, id=fee_id)
    from .forms import PaymentForm
    if request.method == "POST":
        form = PaymentForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                p = form.save(commit=False)
                p.fee = fee
                p.received_by = request.user
                p.save()
                paid = Payment.objects.filter(fee=fee).aggregate(s=Sum("amount"))["s"] or 0
                if paid >= fee.amount:
                    fee.status = "PAID"
                else:
                    fee.status = "PARTIAL"
                fee.save(update_fields=["status"])
            return redirect("core:school_fee_collection")
    else:
        form = PaymentForm(initial={"payment_date": date.today(), "amount": fee.amount})
    return render(request, "core/fees/collect.html", {"form": form, "fee": fee})


@admin_required
def school_fee_receipt_pdf(request, payment_id):
    school = request.user.school
    if not school:
        raise PermissionDenied
    payment = get_object_or_404(Payment, id=payment_id)
    from .pdf_utils import render_pdf_bytes, pdf_response
    pdf_bytes = render_pdf_bytes(
        "core/fees/receipt_pdf.html",
        {"payment": payment, "school": school},
    )
    if not pdf_bytes:
        raise Http404("PDF generation failed")
    return pdf_response(pdf_bytes, f"fee_receipt_{payment.id}.pdf")


# ======================
# Parent Portal (Basic Plan)
# ======================


@parent_required
def parent_dashboard(request):
    parent = getattr(request.user, "parent_profile", None)
    if not parent:
        return render(request, "core/parent/dashboard.html", {"children": []})
    children = list(
        Student.objects.filter(guardians__parent=parent)
        .select_related("user", "classroom", "section")
    )
    return render(request, "core/parent/dashboard.html", {"children": children})


@parent_required
def parent_attendance(request, student_id):
    parent = getattr(request.user, "parent_profile", None)
    if not parent:
        raise PermissionDenied
    student = get_object_or_404(Student, id=student_id)
    if not StudentParent.objects.filter(parent=parent, student=student).exists():
        raise PermissionDenied
    today = date.today()
    from_d = request.GET.get("from_date", today.replace(day=1).isoformat())
    to_d = request.GET.get("to_date", today.isoformat())
    try:
        from_dt = date.fromisoformat(from_d)
        to_dt = date.fromisoformat(to_d)
    except (ValueError, TypeError):
        from_dt = today.replace(day=1)
        to_dt = today
    records = Attendance.objects.filter(
        student=student,
        date__gte=from_dt,
        date__lte=to_dt,
    ).order_by("-date")
    total = records.count()
    present = records.filter(status="PRESENT").count()
    pct = round((present / total * 100) if total else 0, 1)
    return render(request, "core/parent/attendance.html", {
        "student": student,
        "records": records,
        "from_date": from_d,
        "to_date": to_d,
        "total_days": total,
        "present_days": present,
        "percentage": pct,
    })


@parent_required
def parent_marks(request, student_id):
    parent = getattr(request.user, "parent_profile", None)
    if not parent:
        raise PermissionDenied
    student = get_object_or_404(Student, id=student_id)
    if not StudentParent.objects.filter(parent=parent, student=student).exists():
        raise PermissionDenied
    marks_list = Marks.objects.filter(student=student).select_related("subject", "exam").order_by("-exam__start_date", "subject__name")
    exams = _student_exam_summaries(student)
    return render(request, "core/parent/marks.html", {
        "student": student,
        "marks": marks_list,
        "exams": exams,
    })


@parent_required
def parent_announcements(request):
    parent = getattr(request.user, "parent_profile", None)
    if not parent:
        return render(request, "core/parent/announcements.html", {"announcements": []})
    child_ids = list(Student.objects.filter(guardians__parent=parent).values_list("id", flat=True))
    from apps.school_data.models import Homework
    hw = Homework.objects.filter(subject__classroom__students__id__in=child_ids).distinct().order_by("-due_date")[:20] if child_ids else []
    return render(request, "core/parent/announcements.html", {
        "announcements": hw,
        "title": "Homework / Announcements",
    })


# ======================
# Student ID Card PDF (Basic Plan)
# ======================


@admin_required
def school_student_id_card_pdf(request, student_id):
    school = request.user.school
    if not school:
        raise PermissionDenied
    student = get_object_or_404(Student, id=student_id)
    from .pdf_utils import render_pdf_bytes, pdf_response
    qr_data = f"STUDENT:{student.admission_number or student.user.username}:{school.code}"
    qr_b64 = None
    try:
        import qrcode
        import base64
        qr = qrcode.QRCode(version=1, box_size=4, border=2)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception:
        pass
    pdf_bytes = render_pdf_bytes(
        "core/student_id_card.html",
        {"student": student, "school": school, "qr_b64": qr_b64},
    )
    if not pdf_bytes:
        raise Http404("PDF generation failed")
    return pdf_response(pdf_bytes, f"id_card_{student.user.username}.pdf")


# ======================
# Staff Attendance (Basic Plan)
# ======================

STATUS_CHOICES = [
    ("PRESENT", "Present"),
    ("ABSENT", "Absent"),
    ("LEAVE", "Leave"),
    ("HALF_DAY", "Half Day"),
    ("HOLIDAY", "Holiday"),
    ("OTHER", "Other"),
]


def _staff_attendance_date_range(request):
    """Return (start_date, end_date) for staff attendance filter. Default: current month."""
    today = date.today()
    month_str = request.GET.get("month", "")
    start_str = request.GET.get("start_date", "").strip()
    end_str = request.GET.get("end_date", "").strip()
    # Prefer explicit date range; fallback to month; fallback to current month
    if start_str and end_str:
        try:
            first = date.fromisoformat(start_str)
            last = date.fromisoformat(end_str)
            if first > last:
                first, last = last, first
            return first, last
        except (ValueError, TypeError):
            pass
    use_month = month_str or today.strftime("%Y-%m")
    try:
        year, month = map(int, use_month.split("-"))
        first = date(year, month, 1)
        last = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
        return first, last
    except (ValueError, TypeError):
        first = date(today.year, today.month, 1)
        last = today
        return first, last


@admin_required
def school_staff_attendance(request):
    """Staff attendance list: summary cards, date filters, staff table with counts."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    teachers = Teacher.objects.filter(user__school=school).select_related("user").order_by("user__first_name", "user__last_name")
    start_date, end_date = _staff_attendance_date_range(request)
    today = date.today()
    month_default = today.strftime("%Y-%m")
    # Summary counts for date range
    qs = StaffAttendance.objects.filter(
        teacher__user__school=school,
        date__gte=start_date,
        date__lte=end_date,
    )
    summary = qs.values("status").annotate(cnt=Count("id")).values_list("status", "cnt")
    summary_map = dict(summary)
    summary_counts = {
        "present": summary_map.get("PRESENT", 0),
        "absent": summary_map.get("ABSENT", 0),
        "leave": summary_map.get("LEAVE", 0),
        "half_day": summary_map.get("HALF_DAY", 0),
        "holiday": summary_map.get("HOLIDAY", 0),
        "other": summary_map.get("OTHER", 0),
    }
    # Per-staff aggregates
    staff_stats = (
        qs.values("teacher_id", "status")
        .annotate(cnt=Count("id"))
        .order_by("teacher_id")
    )
    status_col_map = {"PRESENT": "present", "ABSENT": "absent", "LEAVE": "leave", "HALF_DAY": "half_day", "HOLIDAY": "holiday", "OTHER": "other"}
    by_teacher = {}
    for row in staff_stats:
        tid = row["teacher_id"]
        if tid not in by_teacher:
            by_teacher[tid] = {"present": 0, "absent": 0, "leave": 0, "half_day": 0, "holiday": 0, "other": 0}
        col = status_col_map.get(row["status"])
        if col:
            by_teacher[tid][col] = row["cnt"]
    staff_rows = []
    for t in teachers:
        row = by_teacher.get(t.id, {"present": 0, "absent": 0, "leave": 0, "half_day": 0, "holiday": 0, "other": 0})
        staff_rows.append({
            "teacher": t,
            "present": row.get("present", 0),
            "absent": row.get("absent", 0),
            "leave": row.get("leave", 0),
            "half_day": row.get("half_day", 0),
            "holiday": row.get("holiday", 0),
            "other": row.get("other", 0),
        })
    from django.core.paginator import Paginator
    paginator = Paginator(staff_rows, 25)
    page = paginator.get_page(request.GET.get("page", 1))
    return render(request, "core/staff_attendance/index.html", {
        "page": page,
        "summary_counts": summary_counts,
        "start_date": start_date,
        "end_date": end_date,
        "month_default": month_default,
        "status_choices": STATUS_CHOICES,
    })


@admin_required
def school_staff_attendance_mark(request):
    """Mark staff attendance for a single date."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    teachers = Teacher.objects.filter(user__school=school).select_related("user").order_by("user__first_name", "user__last_name")
    att_date_str = request.POST.get("date") or request.GET.get("date", date.today().isoformat())
    try:
        att_date = date.fromisoformat(att_date_str)
    except (ValueError, TypeError):
        att_date = date.today()
        att_date_str = att_date.isoformat()
    records = StaffAttendance.objects.filter(
        teacher__user__school=school,
        date=att_date,
    ).select_related("teacher")
    by_teacher = {r.teacher_id: r for r in records}
    if request.method == "POST":
        valid_statuses = {s[0] for s in STATUS_CHOICES}
        for t in teachers:
            key = f"status_{t.id}"
            if key in request.POST:
                status = request.POST[key]
                if status in valid_statuses:
                    StaffAttendance.objects.update_or_create(
                        teacher=t,
                        date=att_date,
                        defaults={"status": status, "marked_by": request.user},
                    )
        return redirect("core:school_staff_attendance")
    staff_rows = []
    for t in teachers:
        rec = by_teacher.get(t.id)
        staff_rows.append({"teacher": t, "current_status": rec.status if rec else "PRESENT"})
    return render(request, "core/staff_attendance/mark.html", {
        "staff_rows": staff_rows,
        "att_date": att_date_str,
        "status_choices": STATUS_CHOICES,
    })


# ======================
# Inventory & Invoicing (Basic Plan)
# ======================


@admin_required
def school_inventory_index(request):
    school = _school_module_check(request, "inventory")
    if not school:
        add_warning_once(request, "inventory_not_available", "Inventory module not available in your plan.")
        return redirect("core:admin_dashboard")
    items = InventoryItem.objects.all()
    purchases = Purchase.objects.all().select_related("inventory_item").order_by("-purchase_date")[:15]
    return render(request, "core/inventory/index.html", {"items": items, "purchases": purchases})


@admin_required
def school_inventory_item_add(request):
    school = _school_module_check(request, "inventory")
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import InventoryItemForm
    if request.method == "POST":
        form = InventoryItemForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.save_with_audit(request.user)
            return redirect("core:school_inventory_index")
    else:
        form = InventoryItemForm()
    return render(request, "core/inventory/item_form.html", {"form": form, "title": "Add Item"})


@admin_required
def school_purchase_add(request):
    school = _school_module_check(request, "inventory")
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import PurchaseForm
    if request.method == "POST":
        form = PurchaseForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.total_amount = (obj.quantity * (obj.unit_price or 0))
            obj.save_with_audit(request.user)
            item = obj.inventory_item
            item.quantity = (item.quantity or 0) + obj.quantity
            item.save(update_fields=["quantity"])
            return redirect("core:school_inventory_index")
    else:
        form = PurchaseForm()
        form.fields["inventory_item"].queryset = InventoryItem.objects.all()
    return render(request, "core/inventory/purchase_form.html", {"form": form})


@admin_required
def school_invoices_list(request):
    school = _school_module_check(request, "inventory")
    if not school:
        return redirect("core:admin_dashboard")
    invoices = Invoice.objects.all().order_by("-issue_date")
    return render(request, "core/inventory/invoices_list.html", {"invoices": invoices})


# ======================
# AI Internal Reports (Basic Plan)
# ======================


@admin_required
def school_ai_reports(request):
    school = _school_module_check(request, "ai_reports")
    if not school:
        add_warning_once(request, "ai_reports_not_available", "AI Reports module not available in your plan.")
        return redirect("core:admin_dashboard")
    # Student performance summary
    marks_qs = Marks.objects.filter(exam__isnull=False)
    by_student = {}
    for m in marks_qs.select_related("student", "subject", "exam"):
        sid = m.student_id
        if sid not in by_student:
            by_student[sid] = {"student": m.student, "total_o": 0, "total_m": 0}
        by_student[sid]["total_o"] += m.marks_obtained
        by_student[sid]["total_m"] += m.total_marks
    perf = []
    for d in by_student.values():
        tm = d["total_m"]
        pct = round((d["total_o"] / tm * 100) if tm else 0, 1)
        perf.append({"student": d["student"], "pct": pct})
    perf.sort(key=lambda x: -x["pct"])

    # Class performance
    by_class = {}
    for m in marks_qs.select_related("student__classroom"):
        cid = m.student.classroom_id if m.student.classroom_id else 0
        if cid not in by_class:
            by_class[cid] = {"name": m.student.classroom.name if m.student.classroom else "Unassigned", "total_o": 0, "total_m": 0, "count": 0}
        by_class[cid]["total_o"] += m.marks_obtained
        by_class[cid]["total_m"] += m.total_marks
        by_class[cid]["count"] += 1
    class_perf = [{"name": v["name"], "pct": round((v["total_o"] / v["total_m"] * 100) if v["total_m"] else 0, 1), "count": v["count"]} for v in by_class.values()]

    # Attendance trends (last 30 days)
    start = date.today() - timedelta(days=30)
    att_qs = Attendance.objects.filter(date__gte=start)
    daily = att_qs.values("date").annotate(
        present=Count("id", filter=Q(status="PRESENT")),
        total=Count("id"),
    ).order_by("date")
    trends = [{"date": d["date"], "present": d["present"], "total": d["total"], "pct": round((d["present"] / d["total"] * 100) if d["total"] else 0, 1)} for d in daily]

    return render(request, "core/ai_reports.html", {
        "student_performance": perf[:20],
        "class_performance": class_perf,
        "attendance_trends": trends,
    })


# ======================
# Support (24/7 Support)
# ======================


@login_required
def school_support_create(request):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    from .forms import SupportTicketForm
    initial = {}
    if school.has_feature("priority_support"):
        initial["priority"] = "PRIORITY"
    if request.method == "POST":
        form = SupportTicketForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.submitted_by = request.user
            obj.save_with_audit(request.user)
            return redirect("core:school_support_create")
    else:
        form = SupportTicketForm(initial=initial)
    tickets = SupportTicket.objects.all().order_by("-created_on")[:10]
    return render(request, "core/support/create.html", {"form": form, "tickets": tickets})


# ======================
# Pro Plan: Online Admissions
# ======================


def online_admission_apply(request, school_code):
    """Public admission form. School must have online_admission feature (Pro plan)."""
    school = get_object_or_404(School, code=school_code)
    if not school.has_feature("online_admission"):
        raise Http404("Online admissions not available for this school.")
    from .forms import OnlineAdmissionForm
    if request.method == "POST":
        form = OnlineAdmissionForm(school, request.POST)
        if form.is_valid():
            from django_tenants.utils import tenant_context
            data = form.cleaned_data
            with tenant_context(school):
                app_num = f"APP{school.code}{OnlineAdmission.objects.count() + 1:05d}"
                OnlineAdmission.objects.create(
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                    email=data["email"],
                    phone=data["phone"],
                    date_of_birth=data["date_of_birth"],
                    parent_name=data["parent_name"],
                    parent_phone=data["parent_phone"],
                    address=data.get("address", ""),
                    applied_class=data.get("applied_class"),
                    application_number=app_num,
                )
            from django.urls import reverse
            return redirect(reverse("core:online_admission_status", kwargs={"school_code": school_code}) + f"?app_no={app_num}")
    else:
        form = OnlineAdmissionForm(school)
    return render(request, "core/admissions/apply.html", {"form": form, "school": school})


def online_admission_status(request, school_code):
    """Check admission status by application number (public)."""
    school = get_object_or_404(School, code=school_code)
    if not school.has_feature("online_admission"):
        raise Http404
    application_number = request.GET.get("app_no", "").strip()
    application = None
    if application_number:
        from django_tenants.utils import tenant_context
        with tenant_context(school):
            application = OnlineAdmission.objects.filter(application_number=application_number).first()
    return render(request, "core/admissions/status.html", {
        "school": school,
        "application": application,
        "application_number": application_number,
    })


@admin_required
def school_admissions_list(request):
    """Admin: list and approve/reject online admissions."""
    school = _school_module_check(request, "online_admission")
    if not school:
        add_warning_once(request, "online_admission_not_available", "Online admissions not available in your plan.")
        return redirect("core:admin_dashboard")
    applications = OnlineAdmission.objects.all().select_related("applied_class").order_by("-created_on")
    return render(request, "core/admissions/admin_list.html", {"applications": applications})


@admin_required
def school_admission_approve(request, pk):
    school = _school_module_check(request, "online_admission")
    if not school:
        raise PermissionDenied
    app = get_object_or_404(OnlineAdmission, pk=pk)
    app.status = "APPROVED"
    app.approved_by = request.user
    app.remarks = request.POST.get("remarks", "")
    app.save()
    return redirect("core:school_admissions_list")


@admin_required
def school_admission_reject(request, pk):
    school = _school_module_check(request, "online_admission")
    if not school:
        raise PermissionDenied
    app = get_object_or_404(OnlineAdmission, pk=pk)
    app.status = "REJECTED"
    app.approved_by = request.user
    app.remarks = request.POST.get("remarks", "")
    app.save()
    return redirect("core:school_admissions_list")


# ======================
# Pro Plan: Online Results (Public - Roll + DOB)
# ======================


def online_results_view(request, school_code):
    """Public results: enter roll number + DOB to view."""
    school = get_object_or_404(School, code=school_code)
    if not school.has_feature("online_results"):
        raise Http404
    roll = request.GET.get("roll", "").strip()
    dob_str = request.GET.get("dob", "").strip()
    student = None
    exams = []
    if roll and dob_str:
        try:
            from django_tenants.utils import tenant_context
            dob = date.fromisoformat(dob_str)
            with tenant_context(school):
                student = Student.objects.filter(
                    roll_number=roll,
                    date_of_birth=dob,
                ).select_related("user", "classroom", "section").first()
                if student:
                    exams = _student_exam_summaries(student)
        except ValueError:
            pass
    return render(request, "core/results/public_results.html", {
        "school": school,
        "roll": roll,
        "dob": dob_str,
        "student": student,
        "exams": exams,
    })


# ======================
# Pro Plan: Topper List
# ======================


@admin_required
def school_toppers(request):
    """Backward-compatible redirect to new reports toppers view."""
    return redirect("core:school_reports_toppers")


@admin_required
def school_reports_toppers(request):
    """Toppers report under Reports module with filters."""
    school = _school_module_check(request, "topper_list")
    if not school:
        add_warning_once(request, "topper_list_not_available", "Topper list not available in your plan.")
        return redirect("core:admin_dashboard")
    from apps.school_data.models import Marks, Exam, ClassRoom, Section
    from django.db.models import Sum
    import json

    exam_id = request.GET.get("exam") or ""
    classroom_id = request.GET.get("classroom") or ""
    section_id = request.GET.get("section") or ""
    top_n = int(request.GET.get("top", "10") or "10")
    if top_n not in (3, 10):
        top_n = 10

    marks_qs = Marks.objects.filter(exam__isnull=False)
    if exam_id.isdigit():
        marks_qs = marks_qs.filter(exam_id=int(exam_id))
    if classroom_id.isdigit():
        marks_qs = marks_qs.filter(student__classroom_id=int(classroom_id))
    if section_id.isdigit():
        marks_qs = marks_qs.filter(student__section_id=int(section_id))

    # Class toppers
    marks_by_class = marks_qs.values("student__classroom__name", "student_id").annotate(
        total_o=Sum("marks_obtained"), total_m=Sum("total_marks")
    )
    by_class = {}
    for m in marks_by_class:
        cname = m["student__classroom__name"] or "Unassigned"
        if cname not in by_class:
            by_class[cname] = []
        pct = round((m["total_o"] / m["total_m"] * 100) if m["total_m"] else 0, 1)
        by_class[cname].append({"student_id": m["student_id"], "pct": pct})
    for c in by_class:
        by_class[c].sort(key=lambda x: -x["pct"])
        by_class[c] = by_class[c][:top_n]
    # Resolve student names
    student_ids = set()
    for v in by_class.values():
        for x in v:
            student_ids.add(x["student_id"])
    students = {s.id: s for s in Student.objects.filter(id__in=student_ids).select_related("user")}
    class_toppers = []
    for cname, rows in sorted(by_class.items()):
        class_toppers.append({
            "class": cname,
            "toppers": [{"student": students.get(r["student_id"]), "pct": r["pct"]} for r in rows],
        })
    # School toppers (top N overall)
    school_agg = marks_qs.values("student_id").annotate(
        total_o=Sum("marks_obtained"), total_m=Sum("total_marks")
    )
    school_list = [
        (x["student_id"], round((x["total_o"] / x["total_m"] * 100) if x["total_m"] else 0, 1))
        for x in school_agg
    ]
    school_list.sort(key=lambda x: -x[1])
    sid_set = {sid for sid, _ in school_list[:top_n]}
    school_students = {s.id: s for s in Student.objects.filter(id__in=sid_set).select_related("user", "classroom", "section")}
    school_toppers_list = [
        {"student": school_students.get(sid), "pct": pct}
        for sid, pct in school_list[:top_n]
        if school_students.get(sid)
    ]
    # Subject toppers (top 3 per subject)
    subj_agg = marks_qs.values(
        "subject_id", "subject__name", "student_id"
    ).annotate(total_o=Sum("marks_obtained"), total_m=Sum("total_marks"))
    by_subj = {}
    for m in subj_agg:
        sname = m["subject__name"] or "Unknown"
        if sname not in by_subj:
            by_subj[sname] = []
        pct = round((m["total_o"] / m["total_m"] * 100) if m["total_m"] else 0, 1)
        by_subj[sname].append((m["student_id"], pct))
    for s in by_subj:
        by_subj[s].sort(key=lambda x: -x[1])
        by_subj[s] = by_subj[s][:3]
    subj_students = {
        s.id: s
        for s in Student.objects.filter(
            id__in={x[0] for v in by_subj.values() for x in v}
        ).select_related("user")
    }
    subject_toppers = [
        {
            "subject": sname,
            "toppers": [
                {"student": subj_students.get(x[0]), "pct": x[1]}
                for x in v
                if subj_students.get(x[0])
            ],
        }
        for sname, v in sorted(by_subj.items())
    ]

    # Chart data for this report (top students + class comparison)
    chart_labels = [
        t["student"].user.get_full_name() or t["student"].user.username
        for t in school_toppers_list
    ]
    chart_values = [t["pct"] for t in school_toppers_list]
    class_chart_labels = [c["class"] for c in class_toppers]
    class_chart_values = [
        round(sum(x["pct"] for x in c["toppers"]) / len(c["toppers"]), 1) if c["toppers"] else 0
        for c in class_toppers
    ]

    exams = Exam.objects.all().order_by("-start_date")
    classes = ClassRoom.objects.all().order_by("name")
    sections = Section.objects.all().order_by("name")

    return render(request, "core/reports/toppers.html", {
        "class_toppers": class_toppers,
        "school_toppers": school_toppers_list,
        "subject_toppers": subject_toppers,
        "exams": exams,
        "classes": classes,
        "sections": sections,
        "selected_exam": exam_id,
        "selected_classroom": classroom_id,
        "selected_section": section_id,
        "top_n": top_n,
        "chart_labels": json.dumps(chart_labels),
        "chart_values": json.dumps(chart_values),
        "class_chart_labels": json.dumps(class_chart_labels),
        "class_chart_values": json.dumps(class_chart_values),
    })


# ======================
# Pro Plan: Library
# ======================


@admin_required
def school_library_index(request):
    school = _school_module_check(request, "library")
    if not school:
        add_warning_once(request, "library_not_available", "Library module not available in your plan.")
        return redirect("core:admin_dashboard")
    books = Book.objects.all()
    issues = BookIssue.objects.all().select_related("book", "student__user").filter(return_date__isnull=True)
    return render(request, "core/library/index.html", {"books": books, "issues": issues})


@admin_required
def school_library_book_add(request):
    school = _school_module_check(request, "library")
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import BookForm
    if request.method == "POST":
        form = BookForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.available_copies = obj.total_copies
            obj.save_with_audit(request.user)
            return redirect("core:school_library_index")
    else:
        form = BookForm()
    return render(request, "core/library/book_form.html", {"form": form})


@admin_required
def school_library_issue(request):
    school = _school_module_check(request, "library")
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import BookIssueForm
    if request.method == "POST":
        form = BookIssueForm(school, request.POST)
        if form.is_valid():
            data = form.cleaned_data
            book = data["book"]
            if book.available_copies < 1:
                pass
            else:
                BookIssue.objects.create(
                    book=book,
                    student=data["student"],
                    issue_date=data["issue_date"],
                    due_date=data["due_date"],
                    school=school,
                )
                book.available_copies -= 1
                book.save(update_fields=["available_copies"])
            return redirect("core:school_library_index")
    else:
        form = BookIssueForm(school)
    return render(request, "core/library/issue_form.html", {"form": form})


@admin_required
def school_library_return(request, issue_id):
    school = _school_module_check(request, "library")
    if not school:
        raise PermissionDenied
    issue = get_object_or_404(BookIssue, id=issue_id)
    if request.method == "POST":
        from decimal import Decimal
        ret_date = date.today()
        issue.return_date = ret_date
        if ret_date > issue.due_date:
            days_late = (ret_date - issue.due_date).days
            issue.late_fee = Decimal(str(days_late * 5))
        issue.save()
        issue.book.available_copies += 1
        issue.book.save(update_fields=["available_copies"])
        return redirect("core:school_library_index")
    return render(request, "core/library/return_confirm.html", {"issue": issue})


# ======================
# Pro Plan: Hostel
# ======================


@admin_required
def school_hostel_index(request):
    school = _school_module_check(request, "hostel")
    if not school:
        add_warning_once(request, "hostel_not_available", "Hostel module not available in your plan.")
        return redirect("core:admin_dashboard")
    hostels = Hostel.objects.all()
    allocations = HostelAllocation.objects.all().select_related("student__user", "room__hostel").filter(end_date__isnull=True)
    return render(request, "core/hostel/index.html", {"hostels": hostels, "allocations": allocations})


@admin_required
def school_hostel_add(request):
    school = _school_module_check(request, "hostel")
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import HostelForm
    if request.method == "POST":
        form = HostelForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.save_with_audit(request.user)
            return redirect("core:school_hostel_index")
    else:
        form = HostelForm()
    return render(request, "core/hostel/hostel_form.html", {"form": form})


@admin_required
def school_hostel_room_add(request, hostel_id):
    school = _school_module_check(request, "hostel")
    if not school:
        raise PermissionDenied
    hostel = get_object_or_404(Hostel, id=hostel_id)
    from .forms import HostelRoomForm
    if request.method == "POST":
        form = HostelRoomForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.hostel = hostel
            obj.save_with_audit(request.user)
            return redirect("core:school_hostel_index")
    else:
        form = HostelRoomForm()
    return render(request, "core/hostel/room_form.html", {"form": form, "hostel": hostel})


@admin_required
def school_hostel_allocate(request):
    school = _school_module_check(request, "hostel")
    if not school:
        return redirect("core:admin_dashboard")
    if request.method == "POST":
        room_id = request.POST.get("room_id")
        student_id = request.POST.get("student_id")
        start_date_str = request.POST.get("start_date")
        if room_id and student_id and start_date_str:
            try:
                room = HostelRoom.objects.get(id=room_id)
                student = Student.objects.get(id=student_id)
                start_date = date.fromisoformat(start_date_str)
                HostelAllocation.objects.create(
                    room=room,
                    student=student,
                    start_date=start_date,
                    school=school,
                )
            except (HostelRoom.DoesNotExist, Student.DoesNotExist, ValueError):
                pass
        return redirect("core:school_hostel_index")
    rooms = HostelRoom.objects.all().select_related("hostel")
    students = Student.objects.all()
    return render(request, "core/hostel/allocate.html", {"rooms": rooms, "students": students})


# ======================
# Pro Plan: Transport
# ======================


@admin_required
def school_transport_index(request):
    school = _school_module_check(request, "transport")
    if not school:
        add_warning_once(request, "transport_not_available", "Transport module not available in your plan.")
        return redirect("core:admin_dashboard")
    routes = Route.objects.all()
    vehicles = Vehicle.objects.all().select_related("route")
    assignments = StudentRouteAssignment.objects.all().select_related("student__user", "route")
    return render(request, "core/transport/index.html", {"routes": routes, "vehicles": vehicles, "assignments": assignments})


@admin_required
def school_transport_route_add(request):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import RouteForm
    if request.method == "POST":
        form = RouteForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.save_with_audit(request.user)
            return redirect("core:school_transport_index")
    else:
        form = RouteForm()
    return render(request, "core/transport/route_form.html", {"form": form})


@admin_required
def school_transport_vehicle_add(request):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import VehicleForm
    if request.method == "POST":
        form = VehicleForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.save_with_audit(request.user)
            return redirect("core:school_transport_index")
    else:
        form = VehicleForm()
        form.fields["route"].queryset = Route.objects.all()
    return render(request, "core/transport/vehicle_form.html", {"form": form})


@admin_required
def school_transport_assign(request):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    if request.method == "POST":
        route_id = request.POST.get("route_id")
        student_id = request.POST.get("student_id")
        vehicle_id = request.POST.get("vehicle_id")
        pickup = request.POST.get("pickup_point", "")
        if route_id and student_id:
            try:
                route = Route.objects.get(id=route_id)
                student = Student.objects.get(id=student_id)
                vehicle = Vehicle.objects.filter(id=vehicle_id).first() if vehicle_id else None
                StudentRouteAssignment.objects.update_or_create(
                    student=student,
                    route=route,
                    defaults={"vehicle": vehicle, "pickup_point": pickup, "school": school},
                )
            except (Route.DoesNotExist, Student.DoesNotExist):
                pass
        return redirect("core:school_transport_index")
    routes = Route.objects.all()
    students = Student.objects.all()
    vehicles = Vehicle.objects.all()
    return render(request, "core/transport/assign.html", {"routes": routes, "students": students, "vehicles": vehicles})


# ======================
# Pro Plan: Custom Branding
# ======================


@admin_required
def school_branding(request):
    school = _school_module_check(request, "custom_branding")
    if not school:
        return redirect("core:admin_dashboard")
    if request.method == "POST":
        school.theme_color = request.POST.get("theme_color", school.theme_color or "#4F46E5")
        school.header_text = request.POST.get("header_text", "")
        school.save()
        return redirect("core:school_branding")
    return render(request, "core/branding.html", {"school": school})

