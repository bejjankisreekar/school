from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import Http404
from django.core.exceptions import PermissionDenied
from datetime import date, timedelta

from django.db.models import Count, Q, Sum
from .models import Student, Teacher, Attendance, Homework, Marks, Subject, ClassRoom
from apps.accounts.decorators import (
    admin_required,
    superadmin_required,
    student_required,
    teacher_required,
)

# ======================
# Public Pages
# ======================

def home(request):
    return render(request, "core/home.html")


# ======================
# Super Admin Dashboard
# ======================

@superadmin_required
def super_admin_dashboard(request):
    return render(request, "core/dashboards/super_admin_dashboard.html")


# ======================
# School Admin Dashboard
# ======================

@admin_required
def admin_dashboard(request):
    school = request.user.school
    if not school:
        return render(request, "core/dashboards/admin_dashboard.html", {
            "total_students": 0,
            "total_teachers": 0,
            "attendance_today": [],
            "attendance_today_count": 0,
            "recent_homework": [],
            "recent_marks": [],
        })
    total_students = Student.objects.filter(user__school=school).count()
    total_teachers = Teacher.objects.filter(user__school=school).count()
    today = date.today()
    attendance_today_qs = Attendance.objects.filter(
        date=today,
        student__user__school=school,
    ).select_related("student", "student__user")
    attendance_today = list(attendance_today_qs)
    recent_homework = list(Homework.objects.filter(subject__school=school).order_by("-id")[:10])
    recent_marks = list(Marks.objects.filter(student__user__school=school).order_by("-id")[:10])
    return render(request, "core/dashboards/admin_dashboard.html", {
        "total_students": total_students,
        "total_teachers": total_teachers,
        "attendance_today": attendance_today,
        "attendance_today_count": len(attendance_today),
        "recent_homework": recent_homework,
        "recent_marks": recent_marks,
    })


# ======================
# Teacher Dashboard
# ======================

@teacher_required
def teacher_dashboard(request):
    teacher = getattr(request.user, "teacher_profile", None)
    assigned_subject = teacher.subject if teacher else None
    return render(request, "core/dashboards/teacher_dashboard.html", {
        "assigned_subject": assigned_subject,
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
        total_subjects = Subject.objects.filter(school=school).count()
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
        homework = list(Homework.objects.filter(subject__school=school).select_related("subject").order_by("due_date")[:20])
    else:
        homework = []

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


@student_required
def student_marks(request):
    return render(request, "core/student_dashboard/marks.html")


@student_required
def student_exams_list(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        return render(request, "core/student/exams_list.html", {"exams": []})
    marks_qs = Marks.objects.filter(student=student).select_related("subject").order_by("-exam_date", "-id")
    # Group by exam_name
    exams_by_name = {}
    for m in marks_qs:
        name = m.exam_name
        if name not in exams_by_name:
            exams_by_name[name] = {
                "exam_name": name,
                "exam_date": m.exam_date,
                "marks": [],
            }
        exams_by_name[name]["marks"].append(m)
    exams = []
    for name, data in exams_by_name.items():
        marks = data["marks"]
        total_obtained = sum(m.marks_obtained for m in marks)
        total_max = sum(m.total_marks for m in marks)
        overall_pct = round((total_obtained / total_max * 100) if total_max else 0, 1)
        exams.append({
            "exam_name": name,
            "exam_date": data["exam_date"],
            "total_subjects": len(marks),
            "overall_pct": overall_pct,
        })
    return render(request, "core/student/exams_list.html", {"exams": exams})


@student_required
def student_exam_detail(request, exam_name):
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    marks_qs = Marks.objects.filter(
        student=student,
        exam_name=exam_name,
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
    return render(request, "core/placeholders/coming_soon.html", {"title": "Reports"})


# ======================
# Teacher Actions
# ======================

@teacher_required
def teacher_students_list(request):
    school = request.user.school
    if not school:
        students = []
    else:
        students = Student.objects.filter(user__school=school).select_related("user")
    return render(request, "core/teacher/students_list.html", {"students": students})


@teacher_required
def create_homework(request):
    from .forms import TeacherHomeworkForm

    teacher = getattr(request.user, "teacher_profile", None)
    if not teacher or not teacher.subject:
        messages.warning(request, "You must be assigned a subject before creating homework.")
        return redirect("core:teacher_dashboard")

    if request.method == "POST":
        form = TeacherHomeworkForm(request.POST)
        if form.is_valid():
            hw = form.save(commit=False)
            hw.teacher = teacher
            hw.subject = teacher.subject
            hw.save()
            messages.success(request, "Homework created successfully.")
            return redirect("core:teacher_dashboard")
    else:
        form = TeacherHomeworkForm()

    return render(request, "core/teacher/homework_form.html", {"form": form, "title": "Create Homework", "subject": teacher.subject})


@teacher_required
def enter_marks(request):
    from .forms import MarksForm

    teacher = getattr(request.user, "teacher_profile", None)
    school = request.user.school
    if not teacher or not school:
        messages.warning(request, "Invalid setup.")
        return redirect("core:teacher_dashboard")

    if request.method == "POST":
        form = MarksForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Marks recorded successfully.")
            return redirect("core:teacher_dashboard")
    else:
        form = MarksForm()
        form.fields["student"].queryset = Student.objects.filter(user__school=school)
        from .models import Subject
        if teacher.subject:
            form.fields["subject"].queryset = Subject.objects.filter(id=teacher.subject_id)
        else:
            form.fields["subject"].queryset = Subject.objects.filter(school=school)

    return render(request, "core/teacher/marks_form.html", {"form": form, "title": "Enter Marks"})


@teacher_required
def teacher_exams(request):
    school = request.user.school
    if not school:
        messages.warning(request, "Invalid setup.")
        return redirect("core:teacher_dashboard")
    # List exams (grouped by exam_name from school's marks)
    marks_qs = Marks.objects.filter(student__user__school=school).values("exam_name", "exam_date").distinct()
    exams_data = {}
    for m in marks_qs:
        name = m["exam_name"]
        if name not in exams_data:
            exams_data[name] = {"exam_name": name, "exam_date": m["exam_date"]}
    exams = list(exams_data.values())
    if request.method == "POST":
        from .forms import MarksForm
        form = MarksForm(request.POST)
        form.fields["student"].queryset = Student.objects.filter(user__school=school)
        form.fields["subject"].queryset = Subject.objects.filter(school=school)
        if form.is_valid():
            form.save()
            messages.success(request, "Marks added successfully.")
            return redirect("core:teacher_exams")
    else:
        from .forms import MarksForm
        form = MarksForm()
        form.fields["student"].queryset = Student.objects.filter(user__school=school)
        form.fields["subject"].queryset = Subject.objects.filter(school=school)
    return render(request, "core/teacher/exams.html", {
        "exams": exams,
        "form": form,
    })


@teacher_required
def teacher_class_analytics(request):
    school = request.user.school
    if not school:
        messages.warning(request, "Invalid setup.")
        return redirect("core:teacher_dashboard")

    students = list(Student.objects.filter(user__school=school).select_related("user"))

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
    subjects = Subject.objects.filter(school=school)
    subject_avgs = []
    for subj in subjects:
        agg = Marks.objects.filter(subject=subj, student__user__school=school).aggregate(
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
    """Get unique (class_name, section) from students and classrooms."""
    choices_class = set()
    choices_section = set()
    # From students
    for s in Student.objects.filter(user__school=school).values_list("grade", "section"):
        if s[0]:
            choices_class.add((s[0], s[0]))
            choices_section.add((s[1] or "A", s[1] or "A"))
    # From classrooms
    for c in ClassRoom.objects.filter(school=school).values_list("name", "section"):
        choices_class.add((c[0], c[0]))
        choices_section.add((c[1], c[1]))
    return sorted(choices_class), sorted(choices_section)


@teacher_required
def bulk_attendance(request):
    """Bulk attendance by class-section. URL: /teacher/attendance/"""
    school = request.user.school
    if not school:
        messages.warning(request, "Invalid setup.")
        return redirect("core:teacher_dashboard")

    today = date.today()
    class_choices, section_choices = _get_class_section_choices(school)

    # POST: Save attendance
    if request.method == "POST":
        class_name = request.POST.get("class_name", "").strip()
        section_val = request.POST.get("section", "").strip()
        date_str = request.POST.get("attendance_date", "")
        if not class_name or not section_val or not date_str:
            messages.error(request, "Class, Section, and Date are required.")
            return redirect("core:bulk_attendance")
        try:
            att_date = date.fromisoformat(date_str)
        except (ValueError, TypeError):
            messages.error(request, "Invalid date.")
            return redirect("core:bulk_attendance")
        if att_date > today:
            messages.error(request, "Cannot mark attendance for future dates.")
            return redirect("core:bulk_attendance")

        # Get students (by classroom or grade+section)
        students = list(
            Student.objects.filter(user__school=school)
            .filter(
                Q(classroom__name=class_name, classroom__section=section_val)
                | Q(classroom__isnull=True, grade=class_name, section=section_val)
            )
            .select_related("user")
            .order_by("roll_number")
        )
        if not students:
            messages.warning(request, "No students found for this class-section.")
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
        messages.success(request, "Attendance saved successfully.")
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
            Student.objects.filter(user__school=school)
            .filter(
                Q(classroom__name=class_name, classroom__section=section_val)
                | Q(classroom__isnull=True, grade=class_name, section=section_val)
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
def mark_attendance(request):
    """Legacy single-student attendance form."""
    from .forms import AttendanceForm

    school = request.user.school
    if not school:
        messages.warning(request, "Invalid setup.")
        return redirect("core:teacher_dashboard")

    if request.method == "POST":
        form = AttendanceForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Attendance marked successfully.")
            return redirect("core:teacher_dashboard")
    else:
        form = AttendanceForm(initial={"date": date.today()})
        form.fields["student"].queryset = Student.objects.filter(user__school=school)

    return render(request, "core/teacher/attendance_form.html", {"form": form, "title": "Mark Attendance"})