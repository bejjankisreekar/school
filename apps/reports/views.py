from datetime import timedelta

from django.contrib import messages
from django.db import connection
from django.db.utils import DatabaseError, InternalError, ProgrammingError
from django.http import HttpResponseForbidden, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.urls import reverse
from urllib.parse import urlencode
from django.views.decorators.http import require_GET

from apps.accounts.decorators import admin_required
from apps.accounts.models import User
from apps.core.utils import add_warning_once, has_feature_access
from apps.core.views import _school_module_check
from apps.school_data.models import AcademicYear, Attendance, Student

from .services.dashboard import build_school_reports_dashboard_context
from .services.student_analytics import build_student_analytics_context
from .services.students_by_class import (
    get_default_academic_year_id,
    get_students_by_class_data,
    parse_academic_year_param,
)


def _reports_redirect_no_school(request):
    if getattr(request.user, "role", None) == User.Roles.SUPERADMIN:
        return redirect("core:super_admin_dashboard")
    return redirect("core:admin_dashboard")


@admin_required
def school_reports_dashboard(request):
    """School Analytics Dashboard — KPI metrics + report shortcuts (see services/dashboard.py)."""
    school = getattr(request.user, "school", None)
    if not school:
        return _reports_redirect_no_school(request)
    context = build_school_reports_dashboard_context(
        school, user=request.user, request=request
    )
    return render(request, "core/reports/dashboard.html", context)


@admin_required
def school_report_student_analytics(request):
    """Student analytics: class/section mix, admissions trend, academic-year KPIs."""
    school = getattr(request.user, "school", None)
    if not school:
        return _reports_redirect_no_school(request)
    if not has_feature_access(school, "reports", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")

    ctx = build_student_analytics_context(school)
    return render(request, "core/reports/student_analytics.html", ctx)


@admin_required
def school_report_students_by_class(request):
    """Bar chart: student count per class; optional Academic Year filter."""
    school = request.user.school
    if not school:
        return _reports_redirect_no_school(request)
    if not has_feature_access(school, "reports", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")

    raw_year = request.GET.get("academic_year")
    if raw_year is None:
        year_id = get_default_academic_year_id(school)
    else:
        year_id = parse_academic_year_param(raw_year, school)

    payload = get_students_by_class_data(school, year_id)
    filter_label = "All academic years"
    if year_id:
        y_obj = AcademicYear.objects.filter(pk=year_id).first()
        if y_obj:
            filter_label = y_obj.name

    academic_years = AcademicYear.objects.order_by("-start_date")

    return render(
        request,
        "core/reports/students_by_class.html",
        {
            "class_rows": payload["class_rows"],
            "chart_labels": payload["chart_labels"],
            "chart_counts": payload["chart_counts"],
            "academic_years": academic_years,
            "selected_year_id": year_id,
            "filter_year_label": filter_label,
        },
    )


@admin_required
@require_GET
def school_report_students_by_class_data(request):
    """JSON for Students by Class chart/table (academic year filter)."""
    school = request.user.school
    if not school:
        return JsonResponse({"error": "Not found"}, status=404)
    if not has_feature_access(school, "reports", user=request.user):
        return JsonResponse({"error": "Forbidden"}, status=403)

    year_id = parse_academic_year_param(request.GET.get("academic_year"), school)
    payload = get_students_by_class_data(school, year_id)
    filter_label = "All academic years"
    if year_id:
        y_obj = AcademicYear.objects.filter(pk=year_id).first()
        if y_obj:
            filter_label = y_obj.name

    rows = [{"name": r["name"], "total": r["total"]} for r in payload["class_rows"]]
    return JsonResponse(
        {
            "chart_labels": payload["chart_labels"],
            "chart_counts": payload["chart_counts"],
            "class_rows": rows,
            "filter_year_label": filter_label,
        }
    )


@admin_required
def school_report_attendance_trend(request):
    """Line chart: attendance % for last 7 days (school-scoped)."""
    school = request.user.school
    if not school:
        return _reports_redirect_no_school(request)
    if not has_feature_access(school, "reports", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    if not has_feature_access(school, "attendance", user=request.user):
        messages.warning(request, "Attendance is not enabled for this school.")
        return redirect("reports:dashboard")

    try:
        if getattr(connection, "needs_rollback", False):
            connection.rollback()
    except Exception:
        pass

    today = timezone.localdate()
    total_students = Student.objects.filter(user__school=school).count()
    trend = []
    error_msg = None

    if not total_students:
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            trend.append(
                {
                    "label": d.strftime("%a %d %b"),
                    "short": d.strftime("%a"),
                    "iso": d.isoformat(),
                    "pct": 0.0,
                    "present": 0,
                }
            )
    else:
        try:
            for i in range(6, -1, -1):
                d = today - timedelta(days=i)
                pres = Attendance.objects.filter(
                    date=d,
                    status=Attendance.Status.PRESENT,
                    student__user__school=school,
                ).count()
                pct = round((pres / total_students * 100), 1)
                trend.append(
                    {
                        "label": d.strftime("%a %d %b"),
                        "short": d.strftime("%a"),
                        "iso": d.isoformat(),
                        "pct": pct,
                        "present": pres,
                    }
                )
        except (ProgrammingError, InternalError, DatabaseError):
            error_msg = "Could not load attendance data. Run migrations if columns are missing."
            try:
                connection.rollback()
            except Exception:
                pass
            trend = []
            for i in range(6, -1, -1):
                d = today - timedelta(days=i)
                trend.append(
                    {
                        "label": d.strftime("%a %d %b"),
                        "short": d.strftime("%a"),
                        "iso": d.isoformat(),
                        "pct": 0.0,
                        "present": 0,
                    }
                )

    chart_short_labels = [x["short"] for x in trend]
    chart_full_labels = [x["label"] for x in trend]
    chart_pcts = [x["pct"] for x in trend]
    chart_presents = [x["present"] for x in trend]

    return render(
        request,
        "core/reports/attendance_trend.html",
        {
            "trend": trend,
            "total_students": total_students,
            "chart_short_labels": chart_short_labels,
            "chart_full_labels": chart_full_labels,
            "chart_pcts": chart_pcts,
            "chart_presents": chart_presents,
            "error_msg": error_msg,
        },
    )


@admin_required
def school_reports_toppers(request):
    """Legacy URL: toppers live on the Analytics dashboard (top_* query params)."""
    school = _school_module_check(request, "topper_list")
    if not school:
        add_warning_once(
            request,
            "topper_list_not_available",
            "Topper list is not available (no school context or module disabled).",
        )
        return _reports_redirect_no_school(request)
    remap = {
        "exam": "top_exam",
        "classroom": "top_classroom",
        "section": "top_section",
        "top": "top_limit",
    }
    pairs = []
    for key in request.GET.keys():
        for v in request.GET.getlist(key):
            pairs.append((remap.get(key, key), v))
    url = reverse("reports:dashboard")
    if pairs:
        url += "?" + urlencode(pairs)
    return HttpResponseRedirect(url)
