from django.shortcuts import render
from datetime import date, timedelta
from apps.accounts.decorators import (
    admin_required,
    teacher_required,
    student_required,
)

# ======================
# Public Pages
# ======================

def home(request):
    return render(request, "core/home.html")


# ======================
# Admin Dashboard
# ======================

@admin_required
def admin_dashboard(request):
    return render(request, "core/dashboards/admin_dashboard.html")


# ======================
# Teacher Dashboard
# ======================

@teacher_required
def teacher_dashboard(request):
    return render(request, "core/dashboards/teacher_dashboard.html")


# ======================
# Student Dashboard
# ======================

@student_required
def student_dashboard(request):
    return render(request, "core/student_dashboard/dashboard.html")


@student_required
def student_profile(request):
    return render(request, "core/student_dashboard/profile.html")


def student_attendance(request):
    weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    # Example: generate calendar dates for this month
    today = date.today()
    start_month = date(today.year, today.month, 1)
    end_month = date(today.year, today.month + 1, 1) if today.month < 12 else date(today.year + 1, 1, 1)
    
    delta = end_month - start_month
    calendar_dates = [start_month + timedelta(days=i) for i in range(delta.days)]
    
    # Example attendance
    attendance_present = [date(today.year, today.month, 1), date(today.year, today.month, 3)]
    attendance_absent = [date(today.year, today.month, 2)]
    
    return render(request, "core/student_dashboard/attendance.html", {
        "weekdays": weekdays,
        "calendar_dates": calendar_dates,
        "attendance_present": attendance_present,
        "attendance_absent": attendance_absent,
    })


@student_required
def student_marks(request):
    return render(request, "core/student_dashboard/marks.html")