from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from datetime import date, timedelta
from calendar import monthrange
from io import BytesIO
import json
from django.utils import timezone

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.core.paginator import Paginator
from django.db.utils import OperationalError, ProgrammingError
from apps.customers.models import School
from apps.school_data.models import (
    Student,
    Teacher,
    Attendance,
    Homework,
    HomeworkSubmission,
    Marks,
    Subject,
    ClassRoom,
    Exam,
    Section,
    ClassSectionSubjectTeacher,
    AcademicYear,
    FeeType,
    FeeStructure,
    Fee,
    Payment,
    Badge,
    StudentBadge,
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
from .utils import (
    add_warning_once,
    has_feature_access,
    get_current_academic_year,
    get_current_academic_year_bounds,
)
from .forms import ContactEnquiryForm
from .models import ContactEnquiry
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
    success = request.GET.get("success") == "1"
    if request.method == "POST":
        form = ContactEnquiryForm(request.POST)
        if form.is_valid():
            enquiry = form.save()
            # Optional email notification (safe: does not break the form if email is misconfigured).
            try:
                from django.core.mail import send_mail
                from django.conf import settings

                recipient = "sreekarbejjanki@gmail.com"
                from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@schoolerp.local")
                subject = "New Contact Enquiry"
                body = (
                    f"Name: {enquiry.name}\n"
                    f"Email: {enquiry.email}\n"
                    f"Phone: {enquiry.phone or ''}\n"
                    f"School Name: {enquiry.school_name or ''}\n"
                    f"\nMessage:\n{enquiry.message}\n"
                )
                send_mail(subject, body, from_email, [recipient], fail_silently=True)
            except Exception:
                pass
            # Redirect to avoid duplicate submission (PRG pattern)
            return redirect(f"{reverse('core:contact')}?success=1")
    else:
        form = ContactEnquiryForm()
    return render(request, "marketing/contact.html", {"form": form, "success": success})


# ======================
# Enquiries (Super Admin)
# ======================


@superadmin_required
def superadmin_enquiries(request):
    """
    Super Admin enquiries list.
    GET /superadmin/enquiries/?status=all|unread|read
    """
    status = (request.GET.get("status") or "all").lower()
    qs = ContactEnquiry.objects.all().order_by("-created_at")
    if status == "unread":
        qs = qs.filter(is_read=False)
    elif status == "read":
        qs = qs.filter(is_read=True)
    return render(
        request,
        "superadmin/enquiries.html",
        {"enquiries": qs, "status": status},
    )


@superadmin_required
def superadmin_enquiry_mark_read(request, enquiry_id: int):
    if request.method != "POST":
        raise PermissionDenied
    enquiry = get_object_or_404(ContactEnquiry, id=enquiry_id)
    if not enquiry.is_read:
        enquiry.is_read = True
        enquiry.save(update_fields=["is_read"])
    return redirect("core:superadmin_enquiries")


@require_GET
def enquiries_unread_count(request):
    """GET /api/enquiries/unread-count/ -> {"unread_count": 5}"""
    if not request.user.is_authenticated or getattr(request.user, "role", None) != User.Roles.SUPERADMIN:
        return JsonResponse({"unread_count": 0}, status=403)
    unread = ContactEnquiry.objects.filter(is_read=False).count()
    return JsonResponse({"unread_count": unread})


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

    # Upcoming exams (date >= today)
    upcoming_exams = list(
        Exam.objects.filter(date__gte=today)
        .order_by("date")[:10]
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

    total_employees = total_teachers

    employees_distribution = [{"label": "Teachers", "count": total_teachers}]

    salary_overview = []
    fee_collection = []
    transport_usage = []
    hostel_occupancy = []
    has_payroll = school and has_feature_access(school, "payroll")
    has_fees = school and has_feature_access(school, "fees")
    has_transport = school and has_feature_access(school, "transport")
    has_hostel = school and has_feature_access(school, "hostel")

    if has_payroll:
        try:
            from apps.payroll.models import Payslip
            salary_today = sum(
                float(p.net_salary) for p in Payslip.objects.filter(
                    payment_date=today, status__in=("PROCESSED", "PAID")
                )
            )
            week_start = today - timedelta(days=today.weekday())
            salary_week = sum(
                float(p.net_salary) for p in Payslip.objects.filter(
                    payment_date__gte=week_start, payment_date__lte=today,
                    status__in=("PROCESSED", "PAID")
                )
            )
            month_start = today.replace(day=1)
            salary_month = sum(
                float(p.net_salary) for p in Payslip.objects.filter(
                    payment_date__gte=month_start, payment_date__lte=today,
                    status__in=("PROCESSED", "PAID")
                )
            )
            year_start = today.replace(month=1, day=1)
            salary_year = sum(
                float(p.net_salary) for p in Payslip.objects.filter(
                    payment_date__gte=year_start, payment_date__lte=today,
                    status__in=("PROCESSED", "PAID")
                )
            )
            salary_overview = [
                {"label": "Today", "amount": salary_today},
                {"label": "Week", "amount": salary_week},
                {"label": "Month", "amount": salary_month},
                {"label": "Year", "amount": salary_year},
            ]
        except Exception:
            pass

    if has_fees:
        try:
            fee_today = Payment.objects.filter(payment_date=today).aggregate(s=Sum("amount"))["s"] or 0
            week_start = today - timedelta(days=today.weekday())
            fee_week = Payment.objects.filter(
                payment_date__gte=week_start, payment_date__lte=today
            ).aggregate(s=Sum("amount"))["s"] or 0
            month_start = today.replace(day=1)
            fee_month = Payment.objects.filter(
                payment_date__gte=month_start, payment_date__lte=today
            ).aggregate(s=Sum("amount"))["s"] or 0
            year_start = today.replace(month=1, day=1)
            fee_year = Payment.objects.filter(
                payment_date__gte=year_start, payment_date__lte=today
            ).aggregate(s=Sum("amount"))["s"] or 0
            fee_collection = [
                {"label": "Today", "amount": float(fee_today)},
                {"label": "Week", "amount": float(fee_week)},
                {"label": "Month", "amount": float(fee_month)},
                {"label": "Year", "amount": float(fee_year)},
            ]
        except Exception:
            pass

    if has_transport:
        try:
            vehicles_cnt = Vehicle.objects.count()
            drivers_cnt = Driver.objects.count()
            routes_cnt = Route.objects.count()
            bus_students = StudentRouteAssignment.objects.count()
            transport_usage = [
                {"label": "Vehicles", "value": vehicles_cnt},
                {"label": "Drivers", "value": drivers_cnt},
                {"label": "Routes", "value": routes_cnt},
                {"label": "Bus Students", "value": bus_students},
            ]
        except Exception:
            pass

    if has_hostel:
        try:
            rooms_cnt = HostelRoom.objects.count()
            beds_cnt = HostelRoom.objects.aggregate(s=Sum("capacity"))["s"] or 0
            hostel_students = HostelAllocation.objects.filter(end_date__isnull=True).count()
            hostel_occupancy = [
                {"label": "Rooms", "value": rooms_cnt},
                {"label": "Beds", "value": int(beds_cnt)},
                {"label": "Occupied", "value": hostel_students},
            ]
        except Exception:
            pass

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
        "total_employees": total_employees,
        "employees_distribution": employees_distribution,
        "salary_overview": salary_overview,
        "fee_collection": fee_collection,
        "transport_usage": transport_usage,
        "hostel_occupancy": hostel_occupancy,
        "has_payroll": has_payroll,
        "has_fees": has_fees,
        "has_transport": has_transport,
        "has_hostel": has_hostel,
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
            "attendance_list": [],
            "total_days": 0,
            "present_days": 0,
            "attendance_percentage": 0,
            "attendance_heatmap": [],
            "academic_year": get_current_academic_year(),
            "calendar_month_label": "",
            "calendar_cells": [],
            "calendar_prev_month": "",
            "calendar_prev_year": "",
            "calendar_next_month": "",
            "calendar_next_year": "",
            "current_streak": 0,
            "best_streak": 0,
            "insight_level": "info",
            "insight_message": "No attendance data available.",
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
            "attendance_pie": {"labels": ["Present", "Absent", "Leave"], "values": [0, 0, 0]},
            "subject_chart_labels": [],
            "subject_chart_data": [],
            "today_classes": [],
        })
    school = request.user.school

    # Academic year filter for attendance (active AcademicYear -> fallback to June-May window)
    active_year = AcademicYear.objects.filter(is_active=True).order_by("-start_date").first()
    if active_year:
        academic_year_label = active_year.name
        ay_start, ay_end = active_year.start_date, active_year.end_date
    else:
        academic_year_label = get_current_academic_year()
        ay_start, ay_end = get_current_academic_year_bounds()
    attendance_year_qs = Attendance.objects.filter(
        student=student,
        date__gte=ay_start,
        date__lte=ay_end,
    )
    attendance_year_records = list(attendance_year_qs.order_by("date"))

    # Attendance percentage for current academic year
    att_stats = attendance_year_qs.aggregate(
        present=Count("id", filter=Q(status="PRESENT")),
        total=Count("id"),
    )
    total_att = att_stats["total"] or 0
    present_att = att_stats["present"] or 0
    attendance_pct = round((present_att / total_att * 100) if total_att > 0 else 0, 1)

    # Attendance % (This Month, inside current academic year)
    today = date.today()
    month_start = today.replace(day=1)
    month_start = max(month_start, ay_start)
    month_end = min(today, ay_end)
    att_month = Attendance.objects.filter(
        student=student,
        date__gte=month_start,
        date__lte=month_end,
    ).aggregate(
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
    att_all = attendance_year_qs.aggregate(
        present=Count("id", filter=Q(status="PRESENT")),
        absent=Count("id", filter=Q(status="ABSENT")),
        leave=Count("id", filter=Q(status="LEAVE")),
    )
    attendance_pie = {
        "labels": ["Present", "Absent", "Leave"],
        "values": [att_all["present"] or 0, att_all["absent"] or 0, att_all["leave"] or 0],
    }

    # Heatmap (compact window for dashboard)
    heatmap_start = max(ay_start, today - timedelta(days=139))
    heatmap_end = min(today, ay_end)
    attendance_heatmap = _build_attendance_heatmap(
        attendance_year_records,
        heatmap_start,
        heatmap_end,
    )
    current_streak, best_streak = _attendance_streaks(attendance_year_records)
    insight_level, insight_message = _attendance_insight(attendance_pct)

    calendar_cells = _build_calendar_data(attendance_year_records, today.year, today.month)
    prev_month_year = today.year if today.month > 1 else today.year - 1
    prev_month = today.month - 1 if today.month > 1 else 12
    next_month_year = today.year if today.month < 12 else today.year + 1
    next_month = today.month + 1 if today.month < 12 else 1

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
        "attendance_list": list(attendance_year_qs.order_by("-date")),
        "total_days": total_att,
        "present_days": present_att,
        "attendance_percentage": attendance_pct,
        "academic_year": academic_year_label,
        "attendance_heatmap": attendance_heatmap,
        "calendar_month_label": date(today.year, today.month, 1).strftime("%B %Y"),
        "calendar_cells": calendar_cells,
        "calendar_prev_month": prev_month,
        "calendar_prev_year": prev_month_year,
        "calendar_next_month": next_month,
        "calendar_next_year": next_month_year,
        "current_streak": current_streak,
        "best_streak": best_streak,
        "insight_level": insight_level,
        "insight_message": insight_message,
        "attendance_pct": attendance_pct,
        "attendance_pct_this_month": attendance_pct_this_month,
        "latest_exam_pct": latest_exam_pct,
        "latest_exam_name": latest_exam_name,
        "total_subjects": total_subjects,
        "overall_pct": overall_pct,
        "attendance_records": list(attendance_year_qs.order_by("-date")[:30]),
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
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied

    def _calculate_profile_completion(_student):
        """Return completion % based on filled student fields."""
        fields = [
            _student.profile_image,
            _student.phone,
            _student.address,
            _student.date_of_birth,
            request.user.email,
        ]
        # Use truthiness to correctly handle empty ImageField / nullable fields.
        filled = sum(1 for f in fields if f)
        total = len(fields)
        return int((filled / total) * 100) if total else 0

    def _assign_student_badges(_student, attendance_pct, avg_pct, current_streak):
        """Award badges based on attendance, marks, and streak."""
        try:
            attendance_star, _ = Badge.objects.get_or_create(
                name="Attendance Star",
                defaults={"description": "Awarded for 90%+ attendance.", "icon": "bi bi-star-fill"},
            )
            perfect_attendance, _ = Badge.objects.get_or_create(
                name="Perfect Attendance",
                defaults={"description": "Awarded for 100% attendance.", "icon": "bi bi-check2-circle"},
            )
            top_performer, _ = Badge.objects.get_or_create(
                name="Top Performer",
                defaults={"description": "Awarded for 85%+ average marks.", "icon": "bi bi-trophy-fill"},
            )
            consistency_king, _ = Badge.objects.get_or_create(
                name="Consistency King",
                defaults={"description": "Awarded for 7+ present-day streak.", "icon": "bi bi-fire"},
            )
        except (OperationalError, ProgrammingError):
            # If tables are temporarily out of sync, keep profile usable.
            return []

        to_award = []
        if attendance_pct >= 90:
            to_award.append(attendance_star)
        if attendance_pct >= 100:
            to_award.append(perfect_attendance)
        if avg_pct >= 85:
            to_award.append(top_performer)
        if current_streak >= 7:
            to_award.append(consistency_king)

        awarded = []
        for badge in to_award:
            try:
                StudentBadge.objects.get_or_create(student=_student, badge=badge)
            except (OperationalError, ProgrammingError):
                continue
            awarded.append(badge)
        return awarded

    # Quick Stats (attendance + exams + marks)
    ay_start, ay_end = get_current_academic_year_bounds()
    attendance_qs = Attendance.objects.filter(student=student, date__gte=ay_start, date__lte=ay_end)
    total_days = attendance_qs.count()
    present_days = attendance_qs.filter(status=Attendance.Status.PRESENT).count()
    attendance_percentage = round((present_days / total_days * 100) if total_days else 0, 1)

    exam_count = (
        Marks.objects.filter(student=student, exam__isnull=False)
        .values("exam_id")
        .distinct()
        .count()
    )
    legacy_exam_count = (
        Marks.objects.filter(student=student, exam__isnull=True)
        .exclude(exam_name__isnull=True)
        .exclude(exam_name="")
        .values("exam_name")
        .distinct()
        .count()
    )
    total_exams = exam_count + legacy_exam_count

    marks_qs = Marks.objects.filter(student=student, total_marks__gt=0)
    totals = marks_qs.aggregate(obtained=Sum("marks_obtained"), total=Sum("total_marks"))
    obtained = totals.get("obtained") or 0
    total_max = totals.get("total") or 0
    avg_marks = round((obtained / total_max * 100) if total_max else 0, 1)

    # Profile completion + badges
    profile_completion = _calculate_profile_completion(student)

    # Compute streak inside the current academic year (same basis as attendance %)
    attendance_year_records = list(
        Attendance.objects.filter(student=student, date__gte=ay_start, date__lte=ay_end).only("date", "status")
    )
    current_streak, _best_streak = _attendance_streaks(attendance_year_records)

    awarded_badges = _assign_student_badges(student, attendance_percentage, avg_marks, current_streak)
    # Convert to stable template-friendly structure
    badges = [{"name": b.name, "icon": b.icon} for b in awarded_badges]

    return render(
        request,
        "core/student_dashboard/profile.html",
        {
            "student": student,
            "profile_completion": profile_completion,
            "attendance_percentage": attendance_percentage,
            "total_exams": total_exams,
            "avg_marks": avg_marks,
            "badges": badges,
        },
    )


@student_required
def edit_profile(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied

    if request.method == "POST":
        phone = (request.POST.get("phone") or "").strip()
        address = (request.POST.get("address") or "").strip()
        profile_image = request.FILES.get("profile_image")

        if phone and len(phone) > 15:
            messages.error(request, "Phone number must be at most 15 characters.")
            return redirect("core:edit_profile_web")

        if profile_image:
            content_type = getattr(profile_image, "content_type", "") or ""
            if not content_type.startswith("image/"):
                messages.error(request, "Please upload a valid image file for profile picture.")
                return redirect("core:edit_profile_web")

        student.phone = phone or None
        student.address = address or None
        if profile_image:
            student.profile_image = profile_image
        student.save(update_fields=["phone", "address", "profile_image"])

        messages.success(request, "Profile updated successfully.")
        return redirect("core:student_profile")

    return render(request, "core/student_dashboard/edit_profile.html", {"student": student})


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


def _build_attendance_heatmap(records, start_date, end_date):
    """Build heatmap day cells between start_date and end_date (inclusive)."""
    by_date = {r.date: r.status for r in records}
    cells = []
    cur = start_date
    while cur <= end_date:
        status = by_date.get(cur)
        if status == "PRESENT":
            css = "present"
            label = "Present"
        elif status == "ABSENT":
            css = "absent"
            label = "Absent"
        elif status == "LEAVE":
            css = "leave"
            label = "Leave"
        else:
            css = "holiday"
            label = "Holiday"
        cells.append(
            {
                "css": css,
                "label": label,
                "title": f"{cur.isoformat()} - {label}",
            }
        )
        cur += timedelta(days=1)
    return cells


def _build_calendar_data(records, year: int, month: int):
    """
    Build month calendar cells with leading/trailing blanks for a 7-column layout.
    Week starts on Sunday.
    """
    by_date = {r.date: r.status for r in records}
    first_day = date(year, month, 1)
    last_day = monthrange(year, month)[1]
    # Python weekday: Mon=0..Sun=6, convert so Sun=0..Sat=6
    leading_blanks = (first_day.weekday() + 1) % 7

    cells = [{"is_blank": True} for _ in range(leading_blanks)]
    for day_num in range(1, last_day + 1):
        cur = date(year, month, day_num)
        status = by_date.get(cur)
        if status == "PRESENT":
            css = "present"
            label = "Present"
        elif status == "ABSENT":
            css = "absent"
            label = "Absent"
        elif status == "LEAVE":
            css = "leave"
            label = "Leave"
        else:
            css = "holiday"
            label = "Holiday"
        cells.append(
            {
                "is_blank": False,
                "day": day_num,
                "css": css,
                "label": label,
                "title": f"{cur.isoformat()} - {label}",
            }
        )

    # Pad to complete final week row
    while len(cells) % 7 != 0:
        cells.append({"is_blank": True})
    return cells


def _attendance_streaks(records):
    """
    Return (current_streak, best_streak) from chronological attendance records.
    Streak counts contiguous PRESENT records.
    """
    present_dates = sorted(r.date for r in records if r.status == "PRESENT")
    if not present_dates:
        return 0, 0

    best = 1
    run = 1
    for i in range(1, len(present_dates)):
        if (present_dates[i] - present_dates[i - 1]).days == 1:
            run += 1
            best = max(best, run)
        else:
            run = 1

    current = 1
    for i in range(len(present_dates) - 1, 0, -1):
        if (present_dates[i] - present_dates[i - 1]).days == 1:
            current += 1
        else:
            break
    return current, best


def _attendance_insight(percentage: float) -> tuple[str, str]:
    """Return (level, message) for attendance insight UI."""
    if percentage < 75:
        return "danger", "Low attendance warning. Try to improve consistency."
    if percentage > 90:
        return "success", "Excellent attendance. Keep up the great work!"
    return "warning", "Good attendance. A little push can make it excellent."


def _build_querystring_without_page(querydict) -> str:
    """Preserve GET filters while paginating (drop `page`)."""
    try:
        q = querydict.copy()
        q.pop("page", None)
        return q.urlencode()
    except Exception:
        return ""


@student_required
def student_attendance(request):
    if not has_feature_access(getattr(request.user, "school", None), "attendance"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")
    student = getattr(request.user, "student_profile", None)
    if not student:
        return render(request, "core/student/attendance.html", {
            "view_type": "monthly",
            "selected_month": "",
            "selected_year": "",
            "month_choices": [],
            "year_choices": [],
            "selected_academic_year": "",
            "academic_year_choices": [],
            "total_days": 0,
            "present_days": 0,
            "absent_days": 0,
            "leave_days": 0,
            "percentage": 0,
            "heatmap_days": [],
            "calendar_month_label": "",
            "calendar_cells": [],
            "current_streak": 0,
            "best_streak": 0,
            "insight_level": "info",
            "insight_message": "No attendance data available.",
            "prev_month": "",
            "prev_year": "",
            "next_month": "",
            "next_year": "",
            "monthly_attendance_data": [],
            "academic_monthly_attendance": [],
            "records": [],
        })

    today = date.today()
    view_type = request.GET.get("view_type", "monthly")
    if view_type not in ("monthly", "academic"):
        view_type = "monthly"

    active_year = AcademicYear.objects.filter(is_active=True).order_by("-start_date").first()
    current_ay_label = get_current_academic_year()
    current_ay_start, current_ay_end = get_current_academic_year_bounds()
    if active_year:
        current_ay_label = active_year.name
        current_ay_start, current_ay_end = active_year.start_date, active_year.end_date

    academic_year_choices = list(
        AcademicYear.objects.order_by("-start_date").values_list("name", flat=True)
    )
    if current_ay_label not in academic_year_choices:
        academic_year_choices.insert(0, current_ay_label)

    month_choices = [(str(i), date(2000, i, 1).strftime("%B")) for i in range(1, 13)]
    year_choices = [str(y) for y in range(today.year - 3, today.year + 2)]
    selected_month = request.GET.get("month") or str(today.month)
    selected_year = request.GET.get("year") or str(today.year)
    selected_academic_year = request.GET.get("academic_year") or current_ay_label
    try:
        month_int = int(selected_month)
        year_int = int(selected_year)
        if month_int < 1 or month_int > 12:
            raise ValueError
    except (ValueError, TypeError):
        month_int = today.month
        year_int = today.year
        selected_month = str(month_int)
        selected_year = str(year_int)

    if view_type == "academic":
        ay_obj = AcademicYear.objects.filter(name=selected_academic_year).order_by("-start_date").first()
        if ay_obj:
            from_dt, to_dt = ay_obj.start_date, ay_obj.end_date
            selected_academic_year = ay_obj.name
        else:
            try:
                start_year_str, end_year_str = selected_academic_year.split("-", 1)
                start_year, end_year = int(start_year_str), int(end_year_str)
                from_dt = date(start_year, 6, 1)
                to_dt = date(end_year, 4, 30)
            except Exception:
                selected_academic_year = current_ay_label
                from_dt, to_dt = current_ay_start, current_ay_end
    else:
        last_day = monthrange(year_int, month_int)[1]
        from_dt = date(year_int, month_int, 1)
        to_dt = date(year_int, month_int, last_day)

    qs = Attendance.objects.filter(
        student=student,
        date__gte=from_dt,
        date__lte=to_dt,
    ).order_by("-date")

    # Table filters (optional, for professional table view)
    filter_from_date = (request.GET.get("from_date") or "").strip()
    filter_to_date = (request.GET.get("to_date") or "").strip()
    filter_status = (request.GET.get("status") or "").strip().upper()
    if filter_from_date:
        try:
            qs = qs.filter(date__gte=date.fromisoformat(filter_from_date))
        except (ValueError, TypeError):
            filter_from_date = ""
    if filter_to_date:
        try:
            qs = qs.filter(date__lte=date.fromisoformat(filter_to_date))
        except (ValueError, TypeError):
            filter_to_date = ""
    if filter_status in ("PRESENT", "ABSENT", "LEAVE"):
        qs = qs.filter(status=filter_status)
    else:
        filter_status = ""

    # Summary stats should reflect the current filtered range
    total_days = qs.count()
    present_days = qs.filter(status="PRESENT").count()
    absent_days = qs.filter(status="ABSENT").count()
    leave_days = qs.filter(status="LEAVE").count()
    percentage = round((present_days / total_days * 100) if total_days > 0 else 0, 2)

    # Pagination
    limit_raw = (request.GET.get("limit") or "10").strip()
    try:
        per_page = int(limit_raw)
        if per_page not in (10, 25, 50):
            per_page = 10
    except (ValueError, TypeError):
        per_page = 10
    page_number = request.GET.get("page")
    paginator = Paginator(qs, per_page)
    attendance_page = paginator.get_page(page_number)
    records = list(attendance_page.object_list)
    heatmap_days = _build_attendance_heatmap(list(reversed(records)), from_dt, to_dt)
    calendar_cells = _build_calendar_data(records, year_int, month_int) if view_type == "monthly" else []
    current_streak, best_streak = _attendance_streaks(sorted(records, key=lambda r: r.date))
    insight_level, insight_message = _attendance_insight(percentage)
    prev_year = year_int if month_int > 1 else year_int - 1
    prev_month = month_int - 1 if month_int > 1 else 12
    next_year = year_int if month_int < 12 else year_int + 1
    next_month = month_int + 1 if month_int < 12 else 1
    monthly_attendance_data = []
    academic_monthly_attendance = []
    if view_type == "monthly":
        by_date = {r.date: r.status for r in records}
        total_days_in_month = monthrange(year_int, month_int)[1]
        for day_num in range(1, total_days_in_month + 1):
            cur = date(year_int, month_int, day_num)
            status = by_date.get(cur)
            if status == "PRESENT":
                css = "present"
                label = "Present"
            elif status == "ABSENT":
                css = "absent"
                label = "Absent"
            elif status == "LEAVE":
                css = "leave"
                label = "Leave"
            elif cur > today:
                css = "future"
                label = "Future / No Data"
            else:
                css = "holiday"
                label = "No Data"
            monthly_attendance_data.append(
                {
                    "day": day_num,
                    "css": css,
                    "label": label,
                    "title": f"{cur.isoformat()} - {label}",
                }
            )
    else:
        by_date = {r.date: r.status for r in records}
        cursor = date(from_dt.year, from_dt.month, 1)
        end_month_start = date(to_dt.year, to_dt.month, 1)
        while cursor <= end_month_start:
            month_last = monthrange(cursor.year, cursor.month)[1]
            month_start = cursor
            month_end = date(cursor.year, cursor.month, month_last)
            segment_start = max(month_start, from_dt)
            segment_end = min(month_end, to_dt)

            days = []
            if segment_start <= segment_end:
                day_ptr = segment_start
                while day_ptr <= segment_end:
                    status = by_date.get(day_ptr)
                    if status == "PRESENT":
                        css = "present"
                        label = "Present"
                    elif status == "ABSENT":
                        css = "absent"
                        label = "Absent"
                    elif status == "LEAVE":
                        css = "leave"
                        label = "Leave"
                    elif day_ptr > today:
                        css = "future"
                        label = "Future / No Data"
                    else:
                        css = "holiday"
                        label = "No Data"
                    days.append(
                        {
                            "day": day_ptr.day,
                            "css": css,
                            "label": label,
                            "title": f"{label} on {day_ptr.day} {cursor.strftime('%b %Y')}",
                        }
                    )
                    day_ptr += timedelta(days=1)

            academic_monthly_attendance.append(
                {
                    "month_label": cursor.strftime("%b %Y"),
                    "days": days,
                }
            )

            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)

    return render(request, "core/student/attendance.html", {
        "view_type": view_type,
        "selected_month": selected_month,
        "selected_year": selected_year,
        "month_choices": month_choices,
        "year_choices": year_choices,
        "selected_academic_year": selected_academic_year,
        "academic_year_choices": academic_year_choices,
        "total_days": total_days,
        "present_days": present_days,
        "absent_days": absent_days,
        "leave_days": leave_days,
        "percentage": percentage,
        "heatmap_days": heatmap_days,
        "calendar_month_label": date(year_int, month_int, 1).strftime("%B %Y") if view_type == "monthly" else selected_academic_year,
        "calendar_cells": calendar_cells,
        "current_streak": current_streak,
        "best_streak": best_streak,
        "insight_level": insight_level,
        "insight_message": insight_message,
        "prev_month": prev_month,
        "prev_year": prev_year,
        "next_month": next_month,
        "next_year": next_year,
        "month_int": month_int,
        "year_int": year_int,
        "monthly_attendance_data": monthly_attendance_data,
        "academic_monthly_attendance": academic_monthly_attendance,
        "attendance_page": attendance_page,
        "records": records,
        "filter_from_date": filter_from_date,
        "filter_to_date": filter_to_date,
        "filter_status": filter_status,
        "per_page": per_page,
        "limit_choices": [10, 25, 50],
        "pagination_qs": _build_querystring_without_page(request.GET),
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
            "exam_date": data["exam"].date,
            "total_subjects": len(marks),
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
    if not has_feature_access(getattr(request.user, "school", None), "exams"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")
    student = getattr(request.user, "student_profile", None)
    if not student:
        return render(request, "core/student/exams_list.html", {"exams": []})
    exams = _student_exam_summaries(student)
    return render(request, "core/student/exams_list.html", {"exams": exams})


@student_required
def student_exam_detail_by_id(request, exam_id):
    """Detail for Exam FK-based marks."""
    if not has_feature_access(getattr(request.user, "school", None), "exams"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    exam = get_object_or_404(Exam, id=exam_id)
    # Student must be in the exam's class+section
    if not student.classroom or not student.section:
        raise PermissionDenied
    if student.classroom.name != exam.class_name or student.section.name != exam.section:
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
        "exam_date": exam.date,
        "overall_pct": overall_pct,
        "grade": grade,
        "marks_rows": rows,
    })


@student_required
def student_exam_detail(request, exam_name):
    """Detail for legacy exam_name-based marks."""
    if not has_feature_access(getattr(request.user, "school", None), "exams"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")
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
    if not has_feature_access(getattr(request.user, "school", None), "reports"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied

    # Exam summaries
    exams = _student_exam_summaries(student)
    # Line chart data (marks trend across exams)
    trend_source = sorted(exams, key=lambda e: (e.get("exam_date") or date.min))
    exam_trend_labels = [e.get("exam_name") or "Exam" for e in trend_source]
    exam_trend_values = [e.get("overall_pct") or 0 for e in trend_source]

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
    # Bar chart data (subject-wise percentage)
    subject_chart_labels = []
    subject_chart_values = []
    for s in subject_stats.values():
        pct = round((s["obtained"] / s["max"] * 100) if s["max"] else 0, 1)
        subject_chart_labels.append(s["subject"].name)
        subject_chart_values.append(pct)

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
        "exam_trend_labels": exam_trend_labels,
        "exam_trend_values": exam_trend_values,
        "subject_chart_labels": subject_chart_labels,
        "subject_chart_values": subject_chart_values,
    }
    return render(request, "core/student/reports.html", context)


def _student_report_card_context(student, exam):
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
        rows.append(
            {
                "subject": m.subject.name,
                "marks_obtained": m.marks_obtained,
                "total_marks": m.total_marks,
                "pct": pct,
                "grade": _grade_from_pct(pct),
            }
        )
    overall_pct = round((total_obtained / total_max * 100) if total_max else 0, 1)
    overall_grade = _grade_from_pct(overall_pct)

    att_qs = Attendance.objects.filter(student=student)
    total_att_days = att_qs.count()
    present_att_days = att_qs.filter(status=Attendance.Status.PRESENT).count()
    attendance_pct = round((present_att_days / total_att_days * 100) if total_att_days else 0, 1)

    school = student.user.school
    academic_year = f"{exam.date.year}-{exam.date.year + 1}" if exam.date else ""

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

    return {
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


def _student_cumulative_context(student, exam_id=""):
    marks_qs = Marks.objects.filter(student=student).select_related("subject", "exam")
    if exam_id:
        marks_qs = marks_qs.filter(exam_id=exam_id)

    subject_stats = {}
    total_obtained = 0
    total_max = 0
    exam_names = set()
    for m in marks_qs:
        key = m.subject_id
        if key not in subject_stats:
            subject_stats[key] = {"subject": m.subject.name, "obtained": 0, "max": 0, "count": 0}
        subject_stats[key]["obtained"] += m.marks_obtained
        subject_stats[key]["max"] += m.total_marks
        subject_stats[key]["count"] += 1
        total_obtained += m.marks_obtained
        total_max += m.total_marks
        exam_names.add(m.exam.name if m.exam else (m.exam_name or "Legacy Exam"))

    subject_rows = []
    for s in sorted(subject_stats.values(), key=lambda x: x["subject"]):
        avg_pct = round((s["obtained"] / s["max"] * 100) if s["max"] else 0, 1)
        subject_rows.append(
            {
                "subject": s["subject"],
                "attempts": s["count"],
                "avg_pct": avg_pct,
                "grade": _grade_from_pct(avg_pct),
            }
        )

    overall_pct = round((total_obtained / total_max * 100) if total_max else 0, 1)
    return {
        "school": student.user.school,
        "student": student,
        "subject_rows": subject_rows,
        "overall_pct": overall_pct,
        "overall_grade": _grade_from_pct(overall_pct),
        "total_obtained": total_obtained,
        "total_max": total_max,
        "total_exams": len(exam_names),
        "selected_exam_id": str(exam_id or ""),
    }


def _student_attendance_context(student, month: int, year: int):
    from collections import defaultdict

    att_qs = Attendance.objects.filter(student=student).order_by("date")
    month_qs = att_qs.filter(date__month=month, date__year=year)
    monthly_total = month_qs.count()
    monthly_present = month_qs.filter(status=Attendance.Status.PRESENT).count()
    monthly_absent = month_qs.filter(status=Attendance.Status.ABSENT).count()
    monthly_leave = month_qs.filter(status=Attendance.Status.LEAVE).count()
    monthly_pct = round((monthly_present / monthly_total * 100) if monthly_total else 0, 1)

    ay_start, ay_end = get_current_academic_year_bounds()
    ay_qs = att_qs.filter(date__gte=ay_start, date__lte=ay_end)
    ay_total = ay_qs.count()
    ay_present = ay_qs.filter(status=Attendance.Status.PRESENT).count()
    ay_absent = ay_qs.filter(status=Attendance.Status.ABSENT).count()
    ay_leave = ay_qs.filter(status=Attendance.Status.LEAVE).count()
    ay_pct = round((ay_present / ay_total * 100) if ay_total else 0, 1)

    monthly = defaultdict(lambda: {"present": 0, "total": 0})
    for r in ay_qs:
        key = r.date.strftime("%Y-%m")
        monthly[key]["total"] += 1
        if r.status == Attendance.Status.PRESENT:
            monthly[key]["present"] += 1
        elif r.status == Attendance.Status.LEAVE:
            monthly[key]["leave"] = monthly[key].get("leave", 0) + 1
        else:
            monthly[key]["absent"] = monthly[key].get("absent", 0) + 1

    monthly_rows = []
    for key in sorted(monthly.keys()):
        y, m = key.split("-")
        from calendar import month_name

        label = f"{month_name[int(m)]} {y}"
        data = monthly[key]
        present = data["present"]
        total = data["total"]
        absent = data.get("absent", 0)
        leave = data.get("leave", 0)
        pct = round((present / total * 100) if total else 0, 1)
        monthly_rows.append(
            {
                "label": label,
                "present": present,
                "absent": absent,
                "leave": leave,
                "total": total,
                "pct": pct,
            }
        )

    return {
        "school": student.user.school,
        "student": student,
        "selected_month": month,
        "selected_year": year,
        "monthly": {
            "total": monthly_total,
            "present": monthly_present,
            "absent": monthly_absent,
            "leave": monthly_leave,
            "percentage": monthly_pct,
        },
        "academic_year": get_current_academic_year(),
        "academic": {
            "total": ay_total,
            "present": ay_present,
            "absent": ay_absent,
            "leave": ay_leave,
            "percentage": ay_pct,
        },
        "monthly_rows": monthly_rows,
    }


@student_required
@feature_required("reports")
def student_report_card_view(request, exam_id):
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    exam = get_object_or_404(Exam, id=exam_id)
    if not student.classroom or not student.section:
        raise PermissionDenied
    if student.classroom.name != exam.class_name or student.section.name != exam.section:
        raise PermissionDenied
    context = _student_report_card_context(student, exam)
    return render(request, "core/student/report_card_view.html", context)


@student_required
@feature_required("reports")
def student_cumulative_report_view(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    exam_id = (request.GET.get("exam") or "").strip()
    context = _student_cumulative_context(student, exam_id)
    context["exam_options"] = _student_exam_summaries(student)
    return render(request, "core/student/cumulative_report_view.html", context)


@student_required
@feature_required("reports")
def student_attendance_report_view(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    today = timezone.localdate()
    month = int(request.GET.get("month") or today.month)
    year = int(request.GET.get("year") or today.year)
    context = _student_attendance_context(student, month, year)
    context["month_choices"] = list(range(1, 13))
    context["year_choices"] = list(range(today.year - 3, today.year + 2))
    return render(request, "core/student/attendance_report_view.html", context)


@student_required
@feature_required("reports")
def student_report_card_pdf(request, exam_id):
    """
    Generate PDF report card for a specific exam for the logged-in student.
    """
    from .pdf_utils import render_pdf_bytes, pdf_response

    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    exam = get_object_or_404(Exam, id=exam_id)
    if not student.classroom or not student.section:
        raise PermissionDenied
    if student.classroom.name != exam.class_name or student.section.name != exam.section:
        raise PermissionDenied
    context = _student_report_card_context(student, exam)

    pdf = render_pdf_bytes("core/student/report_card_pdf.html", context)
    if pdf is None:
        return redirect("core:student_reports")
    filename = f"report-card-{exam.name.replace(' ', '-')}.pdf"
    return pdf_response(pdf, filename)


@student_required
@feature_required("reports")
def student_attendance_report_pdf(request):
    """
    Generate month-wise attendance PDF for the logged-in student.
    """
    from .pdf_utils import render_pdf_bytes, pdf_response
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    today = timezone.localdate()
    month = int(request.GET.get("month") or today.month)
    year = int(request.GET.get("year") or today.year)
    context = _student_attendance_context(student, month, year)
    context.update(
        {
            "total_present": context["academic"]["present"],
            "total_absent": context["academic"]["absent"],
            "total_days": context["academic"]["total"],
            "overall_pct": context["academic"]["percentage"],
        }
    )
    pdf = render_pdf_bytes("core/student/attendance_report_pdf.html", context)
    if pdf is None:
        return redirect("core:student_reports")
    filename = "attendance-report.pdf"
    return pdf_response(pdf, filename)


@student_required
@feature_required("reports")
def student_cumulative_report_pdf(request):
    from .pdf_utils import render_pdf_bytes, pdf_response

    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    exam_id = (request.GET.get("exam") or "").strip()
    context = _student_cumulative_context(student, exam_id)
    context["generated_on"] = timezone.localdate()
    pdf = render_pdf_bytes("core/student/cumulative_report_pdf.html", context)
    if pdf is None:
        return redirect("core:student_cumulative_report_view")
    return pdf_response(pdf, "cumulative-report.pdf")


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
# School Admin: Subject -> Teacher Mappings
# ======================

@admin_required
@feature_required("students")
def school_subject_teacher_mapping(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")

    class_id = request.GET.get("class_id") or request.POST.get("class_id") or ""
    section_id = request.GET.get("section_id") or request.POST.get("section_id") or ""

    selected_class = None
    selected_section = None
    if class_id and section_id:
        selected_class = get_object_or_404(ClassRoom, id=int(class_id))
        selected_section = get_object_or_404(Section, id=int(section_id))
        # Ensure section is part of this class.
        if not selected_class.sections.filter(id=selected_section.id).exists():
            messages.error(request, "Selected section does not belong to the selected class.")
            selected_class = None
            selected_section = None

    # For bulk mapping, we edit mappings for the current class+section selection.
    teachers = (
        Teacher.objects.filter(user__school=school)
        .select_related("user")
        .order_by("user__first_name", "user__last_name")
    )

    subjects = Subject.objects.none()
    mappings_by_subject = {}
    subject_rows = []
    if selected_class and selected_section:
        subjects = Subject.objects.filter(
            Q(classroom=selected_class) | Q(classroom__isnull=True)
        ).order_by("name")
        existing = (
            ClassSectionSubjectTeacher.objects.filter(
                class_obj=selected_class,
                section=selected_section,
            )
            .select_related("teacher__user", "subject")
        )
        mappings_by_subject = {m.subject_id: m for m in existing}
        subject_rows = [
            {
                "subject": subj,
                "mapped_teacher": mappings_by_subject.get(subj.id).teacher if mappings_by_subject.get(subj.id) else None,
            }
            for subj in subjects
        ]

    # Handle single-assignment form (add one mapping).
    if request.method == "POST" and request.POST.get("assign_single"):
        single_class_id = request.POST.get("class_obj")
        single_section_id = request.POST.get("section")
        single_subject_id = request.POST.get("subject")
        single_teacher_id = request.POST.get("teacher")
        if all([single_class_id, single_section_id, single_subject_id, single_teacher_id]):
            try:
                c = get_object_or_404(ClassRoom, id=int(single_class_id))
                s = get_object_or_404(Section, id=int(single_section_id))
                if not c.sections.filter(id=s.id).exists():
                    messages.error(request, "Section does not belong to the selected class.")
                else:
                    subj = get_object_or_404(Subject, id=int(single_subject_id))
                    t = get_object_or_404(Teacher, id=int(single_teacher_id), user__school=school)
                    ClassSectionSubjectTeacher.objects.update_or_create(
                        class_obj=c,
                        section=s,
                        subject=subj,
                        defaults={"teacher": t},
                    )
                    messages.success(request, "Mapping assigned successfully.")
            except (ValueError, TypeError):
                messages.error(request, "Invalid selection. Please try again.")
        else:
            messages.error(request, "Please select Class, Section, Subject, and Teacher.")
        return redirect("core:school_subject_teacher_mapping")

    if request.method == "POST" and selected_class and selected_section:
        # Bulk mapping: restrict updates to the subject list shown on the UI.
        subject_ids = list(subjects.values_list("id", flat=True))
        teachers_by_id = {t.id: t for t in teachers}

        for sid in subject_ids:
            subject = subjects.filter(id=sid).first()
            if not subject:
                continue

            teacher_id_raw = (request.POST.get(f"subject_{sid}", "") or "").strip()
            if not teacher_id_raw:
                # Remove mapping if cleared.
                ClassSectionSubjectTeacher.objects.filter(
                    class_obj=selected_class,
                    section=selected_section,
                    subject_id=sid,
                ).delete()
                continue

            if not teacher_id_raw.isdigit():
                continue

            teacher_id = int(teacher_id_raw)
            teacher = teachers_by_id.get(teacher_id)
            if not teacher:
                continue

            # Upsert mapping (unique constraint prevents duplicates).
            ClassSectionSubjectTeacher.objects.update_or_create(
                class_obj=selected_class,
                section=selected_section,
                subject=subject,
                defaults={"teacher": teacher},
            )

        messages.success(request, "Subject-to-Teacher mappings saved successfully.")
        return redirect(
            "{}?class_id={}&section_id={}".format(
                reverse("core:school_subject_teacher_mapping"),
                selected_class.id,
                selected_section.id,
            )
        )

    # All mappings for the view table (filter by class/section if selected).
    mappings_qs = (
        ClassSectionSubjectTeacher.objects.all()
        .select_related("class_obj", "section", "subject", "teacher__user")
        .order_by("class_obj__name", "section__name", "subject__name")
    )
    if selected_class:
        mappings_qs = mappings_qs.filter(class_obj=selected_class)
    if selected_section:
        mappings_qs = mappings_qs.filter(section=selected_section)

    context = {
        "title": "Subject-Teacher Mapping",
        "classrooms": ClassRoom.objects.all().order_by("academic_year__start_date", "name"),
        "sections": selected_class.sections.all().order_by("name") if selected_class else Section.objects.all().order_by("name"),
        "selected_class": selected_class,
        "selected_section": selected_section,
        "teachers": teachers,
        "subjects": subjects,
        "subject_rows": subject_rows,
        "mappings": mappings_qs,
        "subjects_all": Subject.objects.all().order_by("name"),
        "sections_all": Section.objects.all().order_by("name"),
    }
    return render(request, "core/school/subject_teacher_mapping.html", context)


# ======================
# Placeholder / Coming Soon
# ======================

@login_required
def students_list(request):
    return render(request, "core/placeholders/coming_soon.html", {"title": "Students"})

@login_required
def teachers_list(request):
    # Student view: show only teachers/subjects relevant to student's class+section.
    if getattr(request.user, "role", None) == "STUDENT":
        student = getattr(request.user, "student_profile", None)
        if not student or not student.classroom:
            return render(request, "core/student/teachers_list.html", {"assignments": []})

        subject_qs = (
            Subject.objects.filter(
                Q(classroom=student.classroom) | Q(classroom__isnull=True)
            )
            .select_related("teacher", "teacher__user")
            .prefetch_related("teachers__user")
            .order_by("name")
        )

        seen = set()
        assignments = []
        section_name = student.section.name if student.section else "N/A"
        class_name = student.classroom.name

        for subj in subject_qs:
            if subj.teacher:
                key = (subj.teacher_id, subj.id)
                if key not in seen:
                    seen.add(key)
                    assignments.append(
                        {
                            "teacher": subj.teacher,
                            "subject": subj.name,
                            "class_name": class_name,
                            "section_name": section_name,
                        }
                    )
            for t in subj.teachers.all():
                key = (t.id, subj.id)
                if key not in seen:
                    seen.add(key)
                    assignments.append(
                        {
                            "teacher": t,
                            "subject": subj.name,
                            "class_name": class_name,
                            "section_name": section_name,
                        }
                    )

        assignments.sort(
            key=lambda x: (
                (x["teacher"].user.get_full_name() or x["teacher"].user.username).lower(),
                x["subject"].lower(),
            )
        )
        return render(
            request,
            "core/student/teachers_list.html",
            {"assignments": assignments},
        )

    return render(request, "core/placeholders/coming_soon.html", {"title": "Teachers"})

@login_required
def attendance_list(request):
    if not has_feature_access(getattr(request.user, "school", None), "attendance"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")
    return render(request, "core/placeholders/coming_soon.html", {"title": "Attendance"})

@login_required
def marks_list(request):
    return render(request, "core/placeholders/coming_soon.html", {"title": "Marks"})


@student_required
@feature_required("fees")
def student_fees(request):
    """Student fee tracker: amount, paid/pending status, and due dates."""
    student = getattr(request.user, "student_profile", None)
    if not student:
        return render(
            request,
            "core/student/fees.html",
            {"fee_rows": [], "summary": {"total": 0, "paid": 0, "pending": 0}},
        )

    fee_qs = (
        Fee.objects.filter(student=student)
        .select_related("fee_structure", "fee_structure__fee_type")
        .prefetch_related("payments")
        .order_by("-due_date")
    )

    rows = []
    total_amount = 0
    total_paid = 0
    today = date.today()
    for fee in fee_qs:
        paid_amount = sum(p.amount for p in fee.payments.all())
        pending_amount = max(fee.amount - paid_amount, 0)
        total_amount += fee.amount
        total_paid += paid_amount
        rows.append(
            {
                "fee": fee,
                "paid_amount": paid_amount,
                "pending_amount": pending_amount,
                "is_unpaid": fee.status in ("PENDING", "PARTIAL"),
                "is_overdue": fee.due_date < today and fee.status in ("PENDING", "PARTIAL"),
            }
        )

    summary = {
        "total": total_amount,
        "paid": total_paid,
        "pending": max(total_amount - total_paid, 0),
    }
    return render(request, "core/student/fees.html", {"fee_rows": rows, "summary": summary})


@login_required
def homework_list(request):
    if not has_feature_access(getattr(request.user, "school", None), "homework"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")

    # Admin: redirect to school homework list
    if getattr(request.user, "role", None) == "ADMIN":
        return redirect("core:school_homework_list")

    # Student: homework assigned to their class and section
    if getattr(request.user, "role", None) == "STUDENT":
        student = getattr(request.user, "student_profile", None)
        if not student or not student.classroom:
            return render(
                request,
                "core/student/homework_list.html",
                {
                    "assignments": [],
                    "subjects": [],
                    "summary": {"completed": 0, "pending": 0},
                    "filters": {"subject_id": "", "status": ""},
                },
            )

        # New: homework with classes + sections (student's class and section)
        hw_class_section = []
        if student.section:
            hw_class_section = list(
                Homework.objects.filter(
                    classes=student.classroom,
                    sections=student.section,
                ).prefetch_related("classes", "sections", "assigned_by")
            )
        # Legacy: homework with subject matching student's class
        hw_legacy = Homework.objects.filter(
            subject__isnull=False,
            subject__classroom=student.classroom,
        ).select_related("subject", "teacher", "teacher__user")
        hw_ids_legacy = set(hw_legacy.values_list("id", flat=True))
        hw_new = [h for h in hw_class_section if h.id not in hw_ids_legacy]
        assignments_raw = list(hw_legacy) + hw_new
        assignments_raw.sort(key=lambda h: (h.due_date, -h.id))

        subject_qs = Subject.objects.filter(
            Q(classroom=student.classroom) | Q(classroom__isnull=True)
        ).order_by("name")
        subject_id = (request.GET.get("subject") or "").strip()
        status_filter = (request.GET.get("status") or "").strip().upper()

        if subject_id.isdigit():
            assignments_raw = [h for h in assignments_raw if h.subject_id and h.subject_id == int(subject_id)]

        submission_map = {
            s.homework_id: s
            for s in HomeworkSubmission.objects.filter(
                student=student,
                homework_id__in=[h.id for h in assignments_raw],
            )
        }

        today = date.today()
        rows = []
        completed = pending = 0
        for hw in assignments_raw:
            sub = submission_map.get(hw.id)
            status = sub.status if sub else HomeworkSubmission.Status.PENDING
            if status == HomeworkSubmission.Status.COMPLETED:
                completed += 1
            else:
                pending += 1
            rows.append(
                {
                    "homework": hw,
                    "status": status,
                    "submission": sub,
                    "is_overdue": hw.due_date < today and status != HomeworkSubmission.Status.COMPLETED,
                    "section_name": student.section.name if student.section else "N/A",
                }
            )

        if status_filter in (HomeworkSubmission.Status.COMPLETED, HomeworkSubmission.Status.PENDING):
            rows = [r for r in rows if r["status"] == status_filter]
        else:
            status_filter = ""

        return render(
            request,
            "core/student/homework_list.html",
            {
                "assignments": rows,
                "subjects": list(subject_qs),
                "summary": {"completed": completed, "pending": pending},
                "filters": {"subject_id": subject_id, "status": status_filter},
            },
        )

    # Teacher or other: redirect to create or dashboard
    if getattr(request.user, "role", None) == "TEACHER":
        return redirect("core:create_homework")
    return render(request, "core/placeholders/coming_soon.html", {"title": "Homework"})


@student_required
@feature_required("homework")
@require_POST
def student_homework_submit(request, homework_id):
    """Upload submission file and mark assignment as submitted for current student."""
    student = getattr(request.user, "student_profile", None)
    if not student or not student.classroom:
        messages.error(request, "Student profile is not configured.")
        return redirect("core:homework_list")

    homework = get_object_or_404(
        Homework.objects.prefetch_related("classes", "sections").select_related("subject"),
        id=homework_id,
    )
    # Check access: new model (classes+sections) or legacy (subject)
    if homework.classes.exists() or homework.sections.exists():
        if not student.section_id or not homework.classes.filter(id=student.classroom_id).exists() or not homework.sections.filter(id=student.section_id).exists():
            raise PermissionDenied
    elif homework.subject:
        if homework.subject.classroom_id and homework.subject.classroom_id != student.classroom_id:
            raise PermissionDenied
    else:
        raise PermissionDenied

    upload = request.FILES.get("submission_file")
    if not upload:
        messages.error(request, "Please select a file to submit.")
        return redirect("core:homework_list")

    submission, _ = HomeworkSubmission.objects.get_or_create(
        homework=homework,
        student=student,
        defaults={"status": HomeworkSubmission.Status.PENDING},
    )
    submission.submission_file = upload
    submission.status = HomeworkSubmission.Status.COMPLETED
    submission.submitted_at = timezone.now()
    submission.save(update_fields=["submission_file", "status", "submitted_at"])

    messages.success(request, f"Assignment submitted for '{homework.title}'.")
    return redirect("core:homework_list")


@admin_required
@feature_required("homework")
def school_homework_list(request):
    """Admin: view all homework."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    qs = (
        Homework.objects.all()
        .prefetch_related("classes", "sections")
        .select_related("assigned_by")
        .order_by("-due_date", "-created_at")
    )
    classroom_filter = request.GET.get("classroom", "").strip()
    if classroom_filter.isdigit():
        qs = qs.filter(classes__id=int(classroom_filter))
    return render(
        request,
        "core/school/homework_list.html",
        {
            "homework_list": qs.distinct(),
            "classrooms": ClassRoom.objects.select_related("academic_year").order_by("academic_year__start_date", "name"),
            "filters": {"classroom": classroom_filter},
            "today": date.today(),
        },
    )


@admin_required
@feature_required("homework")
def school_homework_create(request):
    """Admin: create homework with class+section assignment."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import HomeworkCreateForm
    if request.method == "POST":
        form = HomeworkCreateForm(request.POST, user=request.user)
        if form.is_valid():
            hw = form.save(commit=False)
            hw.assigned_by = request.user
            hw.save()
            form.save_m2m()
            messages.success(request, "Homework created successfully.")
            return redirect("core:school_homework_list")
    else:
        form = HomeworkCreateForm(user=request.user)
    return render(request, "core/school/homework_form.html", {"form": form, "title": "Create Homework"})


@login_required
def reports_list(request):
    """Legacy reports entry (student/general)."""
    if not has_feature_access(getattr(request.user, "school", None), "reports"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")
    return render(request, "core/placeholders/coming_soon.html", {"title": "Reports"})


@admin_required
def school_reports_dashboard(request):
    """
    School Reports Dashboard: charts + report cards.
    Uses Marks aggregates for analytics.
    """
    if not has_feature_access(getattr(request.user, "school", None), "reports"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")
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
        qs.values("exam_id", "exam__name")
        .annotate(total_o=Sum("marks_obtained"), total_m=Sum("total_marks"))
        .order_by("exam__date")
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
@feature_required("homework")
def create_homework(request):
    from .forms import HomeworkCreateForm

    teacher = getattr(request.user, "teacher_profile", None)
    if not teacher:
        messages.warning(request, "Teacher profile is not configured.")
        return redirect("core:teacher_dashboard")

    if request.method == "POST":
        form = HomeworkCreateForm(request.POST, user=request.user)
        if form.is_valid():
            hw = form.save(commit=False)
            hw.assigned_by = request.user
            hw.save()
            form.save_m2m()
            messages.success(request, "Homework created successfully.")
            return redirect("core:create_homework")
    else:
        form = HomeworkCreateForm(user=request.user)

    return render(request, "core/teacher/homework_form.html", {"form": form, "title": "Create Homework"})


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
            # Enforce mapping: teacher must be mapped for (student.class+section, subject)
            student = form.cleaned_data.get("student")
            subject = form.cleaned_data.get("subject")
            if (
                not student
                or not subject
                or not student.classroom
                or not student.section
            ):
                return HttpResponseForbidden("This mark entry is not allowed.")

            allowed = ClassSectionSubjectTeacher.objects.filter(
                teacher=teacher,
                class_obj__name__iexact=student.classroom.name,
                section__name__iexact=student.section.name,
                subject=subject,
            ).exists()
            if not allowed:
                return HttpResponseForbidden("This mark entry is not allowed.")

            form.save()
            return redirect("core:teacher_dashboard")
    else:
        form = MarksForm()
        allowed_pairs = _teacher_allowed_class_section_pairs(teacher)
        if allowed_pairs:
            students_q = Q()
            for class_name, section_name in allowed_pairs:
                students_q |= Q(
                    classroom__name__iexact=class_name,
                    section__name__iexact=section_name,
                )
            form.fields["student"].queryset = Student.objects.filter(students_q).select_related("user", "classroom", "section")

        mapped_subject_ids = ClassSectionSubjectTeacher.objects.filter(teacher=teacher).values_list("subject_id", flat=True).distinct()
        form.fields["subject"].queryset = Subject.objects.filter(id__in=mapped_subject_ids).order_by("name")

    return render(request, "core/teacher/marks_form.html", {"form": form, "title": "Enter Marks"})


def _teacher_allowed_class_section_pairs(teacher):
    """Return allowed (class_name, section_name) pairs for this teacher."""
    if not teacher:
        return set()

    pairs = set()
    # Source of truth: explicit mappings only.
    for class_name, section_name in (
        ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
        .values_list("class_obj__name", "section__name")
        .distinct()
    ):
        if class_name and section_name:
            pairs.add((class_name.lower(), section_name.lower()))
    return pairs


def _teacher_exam_access(exam, school, teacher):
    """Teacher can access an exam if assigned to them, or if (class_name, section) is in their scope."""
    if exam is None or not teacher:
        return False
    if getattr(exam, "teacher_id", None) and exam.teacher_id:
        return exam.teacher_id == teacher.id
    allowed_pairs = _teacher_allowed_class_section_pairs(teacher)
    if not exam.class_name or not exam.section:
        return False
    return (exam.class_name.lower(), exam.section.lower()) in allowed_pairs


@teacher_required
@feature_required("exams")
def teacher_exams(request):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")
    teacher = getattr(request.user, "teacher_profile", None)
    # Teacher sees exams: assigned to them OR (class+section in scope when no teacher assigned).
    if teacher:
        assigned_q = Q(teacher=teacher)
        allowed_pairs = _teacher_allowed_class_section_pairs(teacher)
        scope_q = Q()
        for class_name, section_name in allowed_pairs:
            scope_q |= Q(class_name__iexact=class_name, section__iexact=section_name)
        qs = Exam.objects.filter(assigned_q | (Q(teacher__isnull=True) & scope_q))
    else:
        qs = Exam.objects.none()
    exam_objs = list(qs.order_by("-date"))
    return render(request, "core/teacher/exams.html", {
        "exams": exam_objs,
    })


@admin_required
@feature_required("exams")
def school_exams_list(request):
    """
    School Admin: view all exams in the current school (tenant schema).
    Includes exams created by teachers and admins.
    """
    from apps.school_data.models import ClassRoom

    qs = Exam.objects.filter(date__isnull=False, section__isnull=False).order_by("-date")

    class_id = request.GET.get("classroom") or ""
    if class_id:
        try:
            classroom_obj = ClassRoom.objects.get(id=class_id)
            qs = qs.filter(class_name=classroom_obj.name)
        except ClassRoom.DoesNotExist:
            qs = qs.none()

    exams = qs
    classrooms = ClassRoom.objects.all().order_by("name")

    return render(
        request,
        "core/school/exams_list.html",
        {
            "exams": exams,
            "classrooms": classrooms,
            "filters": {"classroom": class_id},
        },
    )


@admin_required
@feature_required("exams")
def school_exam_create(request):
    """
    School Admin: create exam(s) for class + section(s), optionally assign teacher.
    Creates one exam per selected section. Assigned teacher sees it in their list.
    """
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")

    from .forms import SchoolExamCreateForm
    from apps.school_data.models import Teacher

    if request.method == "POST":
        form = SchoolExamCreateForm(school, request.POST)
        if form.is_valid():
            name = form.cleaned_data["name"]
            class_name = form.cleaned_data["class_name"]
            sections = form.cleaned_data["sections"]
            exam_date = form.cleaned_data["date"]
            teacher_id = form.cleaned_data.get("teacher")

            teacher_obj = None
            if teacher_id:
                teacher_obj = Teacher.objects.filter(id=teacher_id, user__school=school).first()

            created = 0
            for section_name in sections:
                Exam.objects.create(
                    name=name,
                    class_name=class_name,
                    section=section_name,
                    date=exam_date,
                    created_by=request.user,
                    teacher=teacher_obj,
                )
                created += 1
            messages.success(request, f"Created {created} exam(s) successfully.")
            return redirect("core:school_exams_list")
    else:
        form = SchoolExamCreateForm(school)

    # Class -> section names for JS filtering
    class_sections = {}
    for c in ClassRoom.objects.prefetch_related("sections").order_by("name"):
        class_sections[c.name] = [s.name for s in c.sections.order_by("name")]

    return render(request, "core/school/exam_create.html", {
        "form": form,
        "class_sections_json": json.dumps(class_sections),
    })


@teacher_required
def teacher_exam_create(request):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")
    if not has_feature_access(school, "exams"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")
    teacher = getattr(request.user, "teacher_profile", None)
    allowed_pairs = (
        list(
            ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
            .values_list("class_obj__name", "section__name")
            .distinct()
        )
        if teacher
        else []
    )
    allowed_pairs = [(c, s) for c, s in allowed_pairs if c and s]
    if not allowed_pairs:
        messages.warning(
            request,
            "You have no class/section assignments. Contact admin to assign you to subjects before creating exams.",
        )
        return redirect("core:teacher_exams")
    if request.method == "POST":
        from .forms import ExamCreateForm
        form = ExamCreateForm(request.POST, allowed_pairs=allowed_pairs)
        if form.is_valid():
            exam = form.save(commit=False)
            exam.created_by = request.user
            if not _teacher_exam_access(exam, school, teacher):
                raise PermissionDenied
            exam.save()
            return redirect("core:teacher_exam_summary", exam_id=exam.id)
    else:
        from .forms import ExamCreateForm
        form = ExamCreateForm(allowed_pairs=allowed_pairs)
    return render(request, "core/teacher/exam_create.html", {"form": form})


@teacher_required
def teacher_exam_summary(request, exam_id):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")
    if not has_feature_access(school, "exams"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")
    exam = get_object_or_404(Exam, id=exam_id)
    teacher = getattr(request.user, "teacher_profile", None)
    if not _teacher_exam_access(exam, school, teacher):
        raise PermissionDenied
    # Get students in exam's class+section
    students = list(
        Student.objects.filter(
            classroom__name=exam.class_name,
            section__name=exam.section,
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
    if not has_feature_access(school, "exams"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")
    exam = get_object_or_404(Exam, id=exam_id)
    teacher = getattr(request.user, "teacher_profile", None)
    if not _teacher_exam_access(exam, school, teacher):
        raise PermissionDenied

    # Subjects allowed by mapping for this exam class+section.
    subject_ids = ClassSectionSubjectTeacher.objects.filter(
        teacher=teacher,
        class_obj__name__iexact=exam.class_name,
        section__name__iexact=exam.section,
    ).values_list("subject_id", flat=True).distinct()
    subjects = Subject.objects.filter(id__in=subject_ids).order_by("name")

    # Students in exam's class+section
    students = list(
        Student.objects.filter(
            classroom__name__iexact=exam.class_name,
            section__name__iexact=exam.section,
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
        if subject.id not in set(subject_ids):
            raise PermissionDenied
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
    if not has_feature_access(school, "attendance"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")

    today = date.today()
    teacher = getattr(request.user, "teacher_profile", None)
    allowed_pairs_raw = []
    if teacher:
        allowed_pairs_raw = list(
            ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
            .values_list("class_obj__name", "section__name")
            .distinct()
        )
    allowed_pairs_lower = {(c.lower(), s.lower()) for c, s in allowed_pairs_raw if c and s}

    # Populate dropdowns from teacher mappings only.
    class_choices = sorted({c for c, _ in allowed_pairs_raw if c})
    section_choices = sorted({s for _, s in allowed_pairs_raw if s})

    # POST: Save attendance
    if request.method == "POST":
        class_name = request.POST.get("class_name", "").strip()
        section_val = request.POST.get("section", "").strip()
        date_str = request.POST.get("attendance_date", "")
        if not class_name or not section_val or not date_str:
            messages.error(request, "Please select class, section, and date before saving attendance.")
            return redirect("core:bulk_attendance")
        try:
            att_date = date.fromisoformat(date_str)
        except (ValueError, TypeError):
            messages.error(request, "Invalid attendance date. Please select a valid date.")
            return redirect("core:bulk_attendance")
        if att_date > today:
            messages.error(request, "Cannot mark attendance for a future date.")
            return redirect("core:bulk_attendance")

        # Enforce mapping: teacher may only mark attendance for mapped class+section.
        if (class_name.lower(), section_val.lower()) not in allowed_pairs_lower:
            return HttpResponseForbidden("You are not allowed to mark attendance for this class/section.")

        try:
            # Get students by classroom + section (correct FK traversal)
            students = list(
                Student.objects.filter(
                    classroom__name__iexact=class_name,
                    section__name__iexact=section_val,
                )
                .select_related("user")
                .order_by("roll_number")
            )
            if not students:
                messages.error(request, f"No students found for class {class_name}-{section_val}.")
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
                if status not in ("PRESENT", "ABSENT", "LEAVE"):
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

            processed_count = len(to_create) + len(to_update)
            if processed_count:
                messages.success(
                    request,
                    f"Attendance marked successfully for {processed_count} students on {att_date}.",
                )
            else:
                messages.info(request, f"No changes needed. Attendance already up to date for {att_date}.")
            return redirect("core:bulk_attendance")
        except Exception:
            messages.error(request, "Failed to mark attendance. Please try again.")
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
        # If mapping scope doesn't include this class+section, show no students.
        if (class_name.lower(), section_val.lower()) not in allowed_pairs_lower:
            students = []
        else:
            students = list(
                Student.objects.filter(
                    classroom__name__iexact=class_name,
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
    if not has_feature_access(school, "attendance"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")

    teacher = getattr(request.user, "teacher_profile", None)
    allowed_pairs_raw = []
    allowed_pairs_lower = set()
    if teacher:
        allowed_pairs_raw = list(
            ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
            .values_list("class_obj__name", "section__name")
            .distinct()
        )
        allowed_pairs_lower = {(c.lower(), s.lower()) for c, s in allowed_pairs_raw if c and s}

    if request.method == "POST":
        form = AttendanceForm(request.POST)
        if form.is_valid():
            student = form.cleaned_data.get("student")
            if not student or not student.classroom or not student.section:
                return HttpResponseForbidden("This attendance entry is not allowed.")

            if (student.classroom.name.lower(), student.section.name.lower()) not in allowed_pairs_lower:
                return HttpResponseForbidden("This attendance entry is not allowed.")

            att = form.save(commit=False)
            att.marked_by = request.user
            att.save()
            return redirect("core:teacher_dashboard")
    else:
        form = AttendanceForm(initial={"date": date.today()})
        if allowed_pairs_raw:
            students_q = Q()
            for class_name, section_name in allowed_pairs_raw:
                students_q |= Q(
                    classroom__name__iexact=class_name,
                    section__name__iexact=section_name,
                )
            form.fields["student"].queryset = Student.objects.filter(students_q).select_related("user", "classroom", "section")
        else:
            form.fields["student"].queryset = Student.objects.none()

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
    marks_list = (
        Marks.objects.filter(student=student, exam__isnull=False)
        .select_related("subject", "exam")
        .order_by("-exam__date", "subject__name")
    )
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
    children = list(Student.objects.filter(guardians__parent=parent).select_related("classroom", "section"))
    if not children:
        return render(request, "core/parent/announcements.html", {"announcements": [], "title": "Homework / Announcements"})
    child_ids = [c.id for c in children]
    # Legacy: subject-based homework for children's classes
    hw_legacy_ids = set(Homework.objects.filter(subject__classroom__students__id__in=child_ids).values_list("id", flat=True))
    # New: class+section homework
    for c in children:
        if c.classroom_id and c.section_id:
            hw_legacy_ids.update(Homework.objects.filter(classes=c.classroom, sections=c.section).values_list("id", flat=True))
    hw = list(Homework.objects.filter(id__in=hw_legacy_ids).prefetch_related("classes", "sections").select_related("subject").order_by("-due_date")[:20])
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
    # Working days in date range (Mon-Fri)
    total_working_days = 0
    d = start_date
    while d <= end_date:
        if d.weekday() not in (5, 6):
            total_working_days += 1
        d += timedelta(days=1)

    staff_rows = []
    for t in teachers:
        row = by_teacher.get(t.id, {"present": 0, "absent": 0, "leave": 0, "half_day": 0, "holiday": 0, "other": 0})
        staff_rows.append({
            "teacher": t,
            "total_days": total_working_days,
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
    detail_month = request.GET.get("month") or start_date.strftime("%Y-%m")
    return render(request, "core/staff_attendance/index.html", {
        "page": page,
        "summary_counts": summary_counts,
        "start_date": start_date,
        "end_date": end_date,
        "month_default": month_default,
        "detail_month": detail_month,
        "status_choices": STATUS_CHOICES,
    })


@admin_required
def school_staff_attendance_detail(request, teacher_id):
    """Staff attendance calendar drill-down: monthly view with color-coded days."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    teacher = get_object_or_404(Teacher, id=teacher_id, user__school=school)
    today = date.today()

    month_str = request.GET.get("month", today.strftime("%Y-%m"))
    try:
        year, month = map(int, month_str.split("-"))
        view_date = date(year, month, 1)
    except (ValueError, TypeError):
        view_date = date(today.year, today.month, 1)
        year, month = view_date.year, view_date.month

    first_day = date(year, month, 1)
    last_day_num = monthrange(year, month)[1]
    last_day = date(year, month, last_day_num)

    records = list(
        StaffAttendance.objects.filter(
            teacher=teacher,
            date__gte=first_day,
            date__lte=last_day,
        ).order_by("date")
    )
    by_date = {r.date: r for r in records}

    leading_blanks = (first_day.weekday() + 1) % 7
    cells = [{"is_blank": True} for _ in range(leading_blanks)]
    for day_num in range(1, last_day_num + 1):
        cur = date(year, month, day_num)
        rec = by_date.get(cur)
        is_weekend = cur.weekday() in (5, 6)
        is_future = cur > today
        if rec:
            status = rec.status
            remarks = rec.remarks or ""
            if status == "PRESENT":
                css, label = "present", "Present"
            elif status == "ABSENT":
                css, label = "absent", "Absent"
            elif status == "LEAVE":
                css, label = "leave", "Leave"
            elif status == "HALF_DAY":
                css, label = "half-day", "Half Day"
            elif status == "HOLIDAY":
                css, label = "holiday", "Holiday"
            else:
                css, label = "other", rec.get_status_display() or "Other"
        elif is_future:
            css, label, remarks = "future", "Future", ""
        elif is_weekend:
            css, label, remarks = "weekend", "Weekend", ""
        else:
            css, label, remarks = "no-data", "Not Marked", ""
        title = f"{cur.strftime('%d %b %Y')} - {label}"
        if remarks:
            title += f" - {remarks}"
        cells.append({
            "is_blank": False,
            "day": day_num,
            "date_iso": cur.isoformat(),
            "css": css,
            "label": label,
            "title": title,
            "remarks": remarks,
        })
    while len(cells) % 7 != 0:
        cells.append({"is_blank": True})

    prev_month = (view_date.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1
    next_month = f"{next_y}-{next_m:02d}"

    summary = {"present": 0, "absent": 0, "leave": 0, "half_day": 0, "holiday": 0, "other": 0}
    working_days = 0
    for d in range(1, last_day_num + 1):
        cur = date(year, month, d)
        if cur.weekday() not in (5, 6) and cur <= today:
            working_days += 1
        rec = by_date.get(cur)
        if rec:
            key = {"PRESENT": "present", "ABSENT": "absent", "LEAVE": "leave",
                   "HALF_DAY": "half_day", "HOLIDAY": "holiday"}.get(rec.status, "other")
            summary[key] = summary.get(key, 0) + 1

    return render(request, "core/staff_attendance/detail.html", {
        "teacher": teacher,
        "calendar_cells": cells,
        "calendar_month_label": first_day.strftime("%B %Y"),
        "prev_month": prev_month,
        "next_month": next_month,
        "summary": summary,
        "working_days": working_days,
        "today": today,
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

    exams = Exam.objects.all().order_by("-date")
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

