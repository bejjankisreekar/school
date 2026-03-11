"""Admin frontend management: Schools, Teachers, Students — SuperAdmin only."""
import re
from django.contrib.auth import get_user_model
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Q
from django.contrib import messages

from apps.accounts.decorators import superadmin_required
from apps.customers.models import School, Plan, Feature
from apps.school_data.models import Teacher, Student, ClassRoom, Section
from .forms import AdminSchoolForm, AdminTeacherForm, AdminStudentForm

User = get_user_model()


def _generate_school_code(name):
    """Auto-generate school code from name, e.g. 'Green Valley' -> 'GV001'."""
    parts = re.sub(r"[^a-zA-Z0-9\s]", "", name).split()
    initials = "".join(p[:1].upper() for p in parts[:3]) or "SCH"
    count = School.objects.filter(code__startswith=initials).count() + 1
    return f"{initials}{count:03d}"


@superadmin_required
def admin_schools_list(request):
    """List all schools."""
    qs = School.objects.all().order_by("name")
    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(
            Q(name__icontains=search) | Q(code__icontains=search) | Q(address__icontains=search)
        )
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))
    return render(request, "admin/schools_list.html", {"schools": page, "search": search})


@superadmin_required
def admin_school_create(request):
    """Create a new school."""
    form = AdminSchoolForm(request.POST or None)
    if form.is_valid():
        from datetime import date, timedelta

        school = form.save(commit=False)
        school.code = _generate_school_code(school.name)
        plan = school.plan
        if plan and (plan.name or "").lower() == "trial":
            school.trial_end_date = date.today() + timedelta(days=plan.duration_days)
        school.save()
        return redirect("admin_manage:schools_list")
    return render(request, "admin/school_form.html", {"form": form, "title": "Create School"})


@superadmin_required
def admin_school_view(request, school_code):
    """View school details (read-only)."""
    school = get_object_or_404(School, code=school_code)
    return render(request, "admin/school_view.html", {"school": school})


@superadmin_required
def admin_school_edit(request, school_code):
    """Edit a school."""
    school = get_object_or_404(School, code=school_code)
    form = AdminSchoolForm(request.POST or None, instance=school)
    if form.is_valid():
        form.save()
        return redirect("admin_manage:schools_list")
    return render(request, "admin/school_form.html", {"form": form, "school": school, "title": "Edit School"})


@superadmin_required
def admin_teachers_list(request):
    """List teachers for a school. Requires ?school=<code>."""
    from django_tenants.utils import tenant_context
    school_code = request.GET.get("school", "").strip()
    schools = School.objects.all().order_by("name")
    teachers = []
    if school_code:
        school = School.objects.filter(code=school_code).first()
        if school:
            with tenant_context(school):
                qs = Teacher.objects.select_related("user", "user__school").order_by("user__first_name", "user__last_name")
                search = request.GET.get("q", "").strip()
                if search:
                    qs = qs.filter(
                        Q(user__first_name__icontains=search)
                        | Q(user__last_name__icontains=search)
                        | Q(user__email__icontains=search)
                        | Q(user__username__icontains=search)
                    )
                paginator = Paginator(qs, 20)
                page = paginator.get_page(request.GET.get("page", 1))
                return render(request, "admin/teachers_list.html", {
                    "teachers": page,
                    "search": search,
                    "school_code": school_code,
                    "schools": schools,
                })
    paginator = Paginator(teachers, 20)
    page = paginator.get_page(1)
    return render(request, "admin/teachers_list.html", {
        "teachers": page,
        "search": "",
        "school_code": school_code,
        "schools": schools,
    })


@superadmin_required
def admin_teacher_create(request):
    """Create a new teacher (User + Teacher profile) with manual password."""
    form = AdminTeacherForm(request.POST or None, for_create=True)
    if form.is_valid():
        data = form.cleaned_data
        school = data["school"]
        username = data["username"].strip()
        base_username = username
        counter = 0
        while User.objects.filter(username=username).exists():
            counter += 1
            username = f"{base_username}{counter}"
        user = User.objects.create_user(
            username=username,
            email=data["email"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            password=data["password"],
            role=User.Roles.TEACHER,
            school=school,
        )
        from django_tenants.utils import tenant_context
        with tenant_context(school):
            teacher = Teacher(
                user=user,
                phone_number=data.get("phone") or "",
                qualification=data.get("qualification") or "",
            )
            teacher.save_with_audit(request.user)
        return redirect("admin_manage:teachers_list")
    return render(request, "admin/teacher_form.html", {"form": form, "title": "Create Teacher"})


@superadmin_required
def admin_teacher_view(request, school_code, teacher_id):
    """View teacher details (read-only)."""
    from django_tenants.utils import tenant_context
    school = get_object_or_404(School, code=school_code)
    with tenant_context(school):
        teacher = get_object_or_404(Teacher, id=teacher_id)
    return render(request, "admin/teacher_view.html", {"teacher": teacher})


@superadmin_required
def admin_teacher_edit(request, school_code, teacher_id):
    """Edit a teacher."""
    from django_tenants.utils import tenant_context
    school = get_object_or_404(School, code=school_code)
    with tenant_context(school):
        teacher = get_object_or_404(Teacher, id=teacher_id)
    user = teacher.user
    form = AdminTeacherForm(
        request.POST or None,
        for_create=False,
        initial={
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": teacher.phone_number or "",
            "qualification": teacher.qualification or "",
            "school": user.school,
        },
    )
    if form.is_valid():
        data = form.cleaned_data
        user.first_name = data["first_name"]
        user.last_name = data["last_name"]
        user.email = data["email"]
        user.school = data["school"]
        user.save()
        teacher.phone_number = data.get("phone") or ""
        teacher.qualification = data.get("qualification") or ""
        teacher.save_with_audit(request.user)
        return redirect("admin_manage:teachers_list")
    return render(request, "admin/teacher_form.html", {"form": form, "teacher": teacher, "title": "Edit Teacher"})


@superadmin_required
def admin_students_list(request):
    """List students for a school. Requires ?school=<code>."""
    from django_tenants.utils import tenant_context
    school_code = request.GET.get("school", "").strip()
    schools = School.objects.all().order_by("name")
    if school_code:
        school = School.objects.filter(code=school_code).first()
        if school:
            with tenant_context(school):
                qs = Student.objects.select_related(
                    "user", "user__school", "classroom", "section"
                ).order_by("user__first_name", "user__last_name")
                search = request.GET.get("q", "").strip()
                if search:
                    qs = qs.filter(
                        Q(user__first_name__icontains=search)
                        | Q(user__last_name__icontains=search)
                        | Q(admission_number__icontains=search)
                    )
                paginator = Paginator(qs, 20)
                page = paginator.get_page(request.GET.get("page", 1))
                return render(request, "admin/students_list.html", {
                    "students": page,
                    "search": search,
                    "school_code": school_code,
                    "schools": schools,
                })
    paginator = Paginator([], 20)
    page = paginator.get_page(1)
    return render(request, "admin/students_list.html", {
        "students": page,
        "search": "",
        "school_code": school_code,
        "schools": schools,
    })


@superadmin_required
def admin_student_create(request):
    """Create a new student (User + Student profile) with manual password."""
    form = AdminStudentForm(request.POST or None, for_create=True)
    if form.is_valid():
        data = form.cleaned_data
        school = data["school"]
        classroom = data.get("classroom")
        section = data.get("section")
        admission = data["admission_number"]
        username = f"std_{school.code}_{admission}".replace(" ", "_")[:150]
        base = username
        counter = 0
        while User.objects.filter(username=username).exists():
            counter += 1
            username = f"{base}{counter}"[:150]
        user = User.objects.create_user(
            username=username,
            email="",
            first_name=data["first_name"],
            last_name=data["last_name"],
            password=data["password"],
            role=User.Roles.STUDENT,
            school=school,
        )
        from django_tenants.utils import tenant_context
        with tenant_context(school):
            student = Student(
                user=user,
                admission_number=admission,
                roll_number=data.get("roll_number") or admission,
                classroom=classroom,
                section=section,
            )
            student.save_with_audit(request.user)
        return redirect("admin_manage:students_list")
    return render(request, "admin/student_form.html", {"form": form, "title": "Create Student"})


@superadmin_required
def admin_student_view(request, school_code, student_id):
    """View student details (read-only)."""
    from django_tenants.utils import tenant_context
    school = get_object_or_404(School, code=school_code)
    with tenant_context(school):
        student = get_object_or_404(Student, id=student_id)
    return render(request, "admin/student_view.html", {"student": student})


@superadmin_required
def admin_student_edit(request, school_code, student_id):
    """Edit a student."""
    from django_tenants.utils import tenant_context
    school = get_object_or_404(School, code=school_code)
    with tenant_context(school):
        student = get_object_or_404(Student, id=student_id)
    user = student.user
    form = AdminStudentForm(
        request.POST or None,
        for_create=False,
        initial={
            "first_name": user.first_name,
            "last_name": user.last_name,
            "admission_number": student.admission_number or "",
            "school": user.school,
            "classroom": student.classroom,
            "section": student.section,
            "roll_number": student.roll_number or "",
        },
    )
    if form.is_valid():
        data = form.cleaned_data
        user.first_name = data["first_name"]
        user.last_name = data["last_name"]
        user.school = data["school"]
        user.save()
        student.admission_number = data["admission_number"]
        student.roll_number = data.get("roll_number") or data["admission_number"]
        student.classroom = data.get("classroom")
        student.section = data.get("section")
        with tenant_context(school):
            student.save_with_audit(request.user)
        return redirect("admin_manage:students_list")
    return render(request, "admin/student_form.html", {"form": form, "student": student, "title": "Edit Student"})


# ======================
# School Plans (SaaS)
# ======================


@superadmin_required
def admin_school_plans_list(request):
    """School Plans section: list schools with current plan and enabled features."""
    schools = School.objects.exclude(schema_name="public").select_related("saas_plan").order_by("name")
    plans = Plan.objects.prefetch_related("features").order_by("price_per_student")
    return render(request, "admin/school_plans_list.html", {
        "schools": schools,
        "plans": plans,
    })


@superadmin_required
def admin_school_change_plan(request, school_code):
    """Change a school's plan. Upgrade/downgrade applies immediately."""
    school = get_object_or_404(School, code=school_code)
    plans = Plan.objects.prefetch_related("features").order_by("price_per_student")
    if request.method == "POST":
        plan_id = request.POST.get("plan")
        if plan_id:
            plan = get_object_or_404(Plan, pk=plan_id)
            school.saas_plan = plan
            school.enabled_features_override = None  # Reset override when changing plan
            school.save()
            messages.success(request, f"Plan updated to {plan.name}. Changes apply immediately.")
        else:
            school.saas_plan = None
            school.enabled_features_override = None
            school.save()
            messages.success(request, "Plan cleared. School will use legacy plan if any.")
        return redirect("admin_manage:school_plans_list")
    return render(request, "admin/school_change_plan.html", {
        "school": school,
        "plans": plans,
    })


@superadmin_required
def admin_school_manage_features(request, school_code):
    """Enable/disable features per school. Overrides plan defaults."""
    school = get_object_or_404(School, code=school_code)
    features = Feature.objects.all().order_by("name")
    # Current enabled codes: override or plan defaults
    if school.enabled_features_override is not None:
        enabled_codes = set(school.enabled_features_override)
    elif school.saas_plan:
        enabled_codes = school.saas_plan.get_feature_codes()
    else:
        enabled_codes = set()
    if request.method == "POST":
        selected = request.POST.getlist("features")
        school.enabled_features_override = selected
        school.save()
        messages.success(request, f"Features updated for {school.name}. Changes apply immediately.")
        return redirect("admin_manage:school_plans_list")
    return render(request, "admin/school_manage_features.html", {
        "school": school,
        "features": features,
        "enabled_codes": enabled_codes,
    })
