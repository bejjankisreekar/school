"""REST API for Pro Plan schools and admin endpoints."""
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django_tenants.utils import tenant_context

from apps.customers.models import School
from apps.school_data.models import Student, Fee, Payment, Marks, Exam, ClassRoom, Section, Attendance


def _get_school_pro(school_code: str) -> School | None:
    try:
        school = School.objects.get(code=school_code)
        if not school.has_feature("api_access"):
            return None
        return school
    except School.DoesNotExist:
        return None


def _student_json(s):
    return {
        "id": s.id,
        "name": s.user.get_full_name() or s.user.username,
        "roll_number": s.roll_number,
        "admission_number": s.admission_number or "",
        "class": s.classroom.name if s.classroom else "",
        "section": s.section.name if s.section else "",
    }


@require_GET
def api_students(request, school_code: str):
    """GET /api/<school_code>/students/ - List students (Pro plan only)."""
    school = _get_school_pro(school_code)
    if not school:
        return JsonResponse({"error": "School not found or API not available"}, status=404)
    with tenant_context(school):
        students = Student.objects.select_related("user", "classroom", "section").all()
        return JsonResponse({
            "school": school.name,
            "students": [_student_json(s) for s in students],
        })


@require_GET
def api_fees(request, school_code: str):
    """GET /api/<school_code>/fees/ - List fee dues (Pro plan only)."""
    school = _get_school_pro(school_code)
    if not school:
        return JsonResponse({"error": "School not found or API not available"}, status=404)
    with tenant_context(school):
        fees = Fee.objects.select_related("student__user", "fee_structure__fee_type").all()
        paid = {f.id: sum(p.amount for p in f.payments.all()) for f in fees}
        data = []
        for f in fees:
            data.append({
                "id": f.id,
                "student": f.student.user.get_full_name() or f.student.user.username,
                "fee_type": f.fee_structure.fee_type.name,
                "amount": float(f.amount),
                "paid": float(paid.get(f.id, 0)),
                "due_date": str(f.due_date),
                "status": f.status,
            })
        return JsonResponse({"school": school.name, "fees": data})


@require_GET
def api_results(request, school_code: str):
    """GET /api/<school_code>/results/?exam_id=1 - List results for exam (Pro plan only)."""
    school = _get_school_pro(school_code)
    if not school:
        return JsonResponse({"error": "School not found or API not available"}, status=404)
    exam_id = request.GET.get("exam_id")
    if not exam_id:
        return JsonResponse({"error": "exam_id required"}, status=400)
    with tenant_context(school):
        try:
            exam = Exam.objects.get(id=exam_id)
        except Exam.DoesNotExist:
            return JsonResponse({"error": "Exam not found"}, status=404)
        marks_qs = Marks.objects.filter(exam=exam).select_related("student__user", "subject")
        by_student = {}
        for m in marks_qs:
            sid = m.student_id
            if sid not in by_student:
                by_student[sid] = {"student": m.student, "marks": [], "total_o": 0, "total_m": 0}
            by_student[sid]["marks"].append({
                "subject": m.subject.name,
                "obtained": m.marks_obtained,
                "total": m.total_marks,
            })
            by_student[sid]["total_o"] += m.marks_obtained
            by_student[sid]["total_m"] += m.total_marks
        data = []
        for d in by_student.values():
            tm = d["total_m"]
            pct = round((d["total_o"] / tm * 100) if tm else 0, 1)
            data.append({
                "student": d["student"].user.get_full_name() or d["student"].user.username,
                "roll_number": d["student"].roll_number,
                "marks": d["marks"],
                "total_obtained": d["total_o"],
                "total_marks": d["total_m"],
                "percentage": pct,
            })
        return JsonResponse({
            "school": school.name,
            "exam": exam.name,
            "results": data,
        })


@require_GET
def api_attendance(request, school_code: str):
    """GET /api/<school_code>/attendance/?date=YYYY-MM-DD or ?student_id=N - Attendance (Pro plan only)."""
    school = _get_school_pro(school_code)
    if not school:
        return JsonResponse({"error": "School not found or API not available"}, status=404)
    date_str = request.GET.get("date")
    student_id = request.GET.get("student_id")
    with tenant_context(school):
        qs = Attendance.objects.select_related("student__user").all()
        if date_str:
            qs = qs.filter(date=date_str)
        if student_id:
            qs = qs.filter(student_id=student_id)
        qs = qs.order_by("-date", "student__roll_number")[:500]
        data = [
            {
                "student_id": a.student_id,
                "student": a.student.user.get_full_name() or a.student.user.username,
                "roll_number": a.student.roll_number,
                "date": str(a.date),
                "status": a.status,
            }
            for a in qs
        ]
        return JsonResponse({"school": school.name, "attendance": data})


@require_GET
def api_admin_classrooms(request, school_code: str):
    """GET /api/admin/schools/<school_code>/classrooms/ - For AdminStudentForm (super admin)."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return JsonResponse({"error": "Unauthorized"}, status=401)
    try:
        school = School.objects.get(code=school_code)
    except School.DoesNotExist:
        return JsonResponse({"error": "School not found"}, status=404)
    with tenant_context(school):
        classrooms = ClassRoom.objects.select_related("academic_year").order_by("academic_year", "name")
        return JsonResponse({
            "classrooms": [{"id": c.id, "name": str(c)} for c in classrooms],
        })


@require_GET
def api_admin_classrooms_by_id(request, school_id: int):
    """GET /api/admin/schools/by-id/<id>/classrooms/ - For AdminStudentForm (uses pk from select)."""
    try:
        school = School.objects.get(pk=school_id)
    except School.DoesNotExist:
        return JsonResponse({"error": "School not found"}, status=404)
    return api_admin_classrooms(request, school.code)


@require_GET
def api_admin_sections_by_id(request, school_id: int):
    """GET /api/admin/schools/by-id/<id>/sections/?classroom_id=X - For AdminStudentForm."""
    try:
        school = School.objects.get(pk=school_id)
    except School.DoesNotExist:
        return JsonResponse({"error": "School not found"}, status=404)
    return api_admin_sections(request, school.code)


@require_GET
def api_admin_sections(request, school_code: str):
    """GET /api/admin/schools/<school_code>/sections/?classroom_id=X - For AdminStudentForm."""
    try:
        school = School.objects.get(code=school_code)
    except School.DoesNotExist:
        return JsonResponse({"error": "School not found"}, status=404)
    classroom_id = request.GET.get("classroom_id")
    with tenant_context(school):
        qs = Section.objects.select_related("classroom").order_by("classroom", "name")
        if classroom_id:
            qs = qs.filter(classroom_id=classroom_id)
        return JsonResponse({
            "sections": [{"id": s.id, "name": s.name, "classroom_id": s.classroom_id} for s in qs],
        })
