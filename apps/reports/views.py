from datetime import timedelta

from django.contrib import messages
from django.db import connection
from django.db.models import Sum
from django.db.utils import DatabaseError, InternalError, ProgrammingError
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounts.decorators import admin_required
from apps.core.utils import add_warning_once, has_feature_access
from apps.core.views import _exam_read_qs, _school_module_check
from apps.school_data.models import AcademicYear, Attendance, ClassRoom, Section, Student

from .services.dashboard import build_school_reports_dashboard_context
from .services.student_analytics import build_student_analytics_context
from .services.students_by_class import (
    get_default_academic_year_id,
    get_students_by_class_data,
    parse_academic_year_param,
)


@admin_required
def school_reports_dashboard(request):
    """School Analytics Dashboard — KPI metrics + report shortcuts (see services/dashboard.py)."""
    school = getattr(request.user, "school", None)
    if not school:
        return redirect("core:admin_dashboard")
    context = build_school_reports_dashboard_context(school)
    return render(request, "core/reports/dashboard.html", context)


@admin_required
def school_report_student_analytics(request):
    """Student analytics: class/section mix, admissions trend, academic-year KPIs."""
    school = getattr(request.user, "school", None)
    if not school:
        return redirect("core:admin_dashboard")
    if not has_feature_access(school, "reports"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")

    ctx = build_student_analytics_context(school)
    return render(request, "core/reports/student_analytics.html", ctx)


@admin_required
def school_report_students_by_class(request):
    """Bar chart: student count per class; optional Academic Year filter."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    if not has_feature_access(school, "reports"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")

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
    if not has_feature_access(school, "reports"):
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
        return redirect("core:admin_dashboard")
    if not has_feature_access(school, "reports"):
        return HttpResponseForbidden("Upgrade your plan to access this feature")
    if not has_feature_access(school, "attendance"):
        messages.warning(request, "Attendance is not enabled for your plan.")
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
    """Toppers report under Reports module with filters."""
    school = _school_module_check(request, "topper_list")
    if not school:
        add_warning_once(request, "topper_list_not_available", "Topper list not available in your plan.")
        return redirect("core:admin_dashboard")
    from apps.school_data.models import Marks
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

    exams = _exam_read_qs().order_by("-date")
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
