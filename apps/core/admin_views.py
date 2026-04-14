"""Admin frontend management: Schools, Teachers, Students — SuperAdmin only."""
from datetime import date
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_tenants.utils import get_public_schema_name, schema_context

from apps.accounts.decorators import superadmin_required
from apps.core.tenant_provisioning import (
    generate_unique_school_code_from_name,
    schema_name_for_school_code,
)
from apps.customers.models import Coupon, Feature, Plan, School, SchoolSubscription, SubscriptionPlan
from apps.school_data.models import Teacher, Student, ClassRoom, Section, Subject
from .forms import AdminCouponForm, AdminSchoolForm, AdminTeacherForm, AdminStudentForm

User = get_user_model()


def _parse_optional_date(value: str) -> date | None:
    v = (value or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _sync_school_subscription_from_saas(school: School) -> None:
    """
    Keep internal billing row (customers.SubscriptionPlan) aligned with Starter / Enterprise.
    Does not override an active trial.
    """
    if school.plan and (school.plan.name or "").lower() == "trial":
        end = getattr(school, "trial_end_date", None)
        if end and end >= date.today():
            return
    if not school.saas_plan:
        return
    tier = (school.saas_plan.name or "").strip().lower()
    if tier in ("enterprise", "advance"):  # advance: legacy plan name
        sp = SubscriptionPlan.objects.filter(name__iexact="pro", is_active=True).first()
    else:
        sp = SubscriptionPlan.objects.filter(name__iexact="basic", is_active=True).first()
    if sp:
        school.plan = sp
        if (sp.name or "").lower() != "trial":
            school.trial_end_date = None


def _rollback_safe():
    try:
        if not connection.in_atomic_block:
            connection.rollback()
    except Exception:
        pass


def _platform_school_teacher_student_cards():
    """
    Per-tenant teacher/student counts for Super Admin overview (school cards + totals).
    """
    from django_tenants.utils import tenant_context

    schools = School.objects.exclude(schema_name="public").order_by("name")
    cards = []
    total_teachers = total_students = 0
    for school in schools:
        tc = sc = 0
        try:
            with tenant_context(school):
                with transaction.atomic():
                    tc = Teacher.objects.count()
                    sc = Student.objects.count()
        except DatabaseError:
            _rollback_safe()
        cards.append({"school": school, "teacher_count": tc, "student_count": sc})
        total_teachers += tc
        total_students += sc
    return cards, total_teachers, total_students


def _filter_query(request, exclude=("page",)):
    return urlencode([(k, v) for k, v in request.GET.items() if k not in exclude and v != ""])


def _materialize_page_object_list(page):
    """
    Evaluate Page.object_list while still inside tenant_context.

    django-tenants: Paginator.get_page() keeps a lazy queryset slice; the template
    iterates after the context manager exits, so queries run on the public schema
    where tenant tables (e.g. school_data_student) do not exist.
    """
    page.object_list = list(page.object_list)
    return page


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


@transaction.non_atomic_requests
@superadmin_required
def admin_school_create(request):
    """Create a new school (tenant schema + migrations; avoid ATOMIC_REQUESTS conflict)."""
    form = AdminSchoolForm(request.POST or None)
    if form.is_valid():
        school = form.save(commit=False)
        try:
            school.code = generate_unique_school_code_from_name(school.name)
            school.schema_name = schema_name_for_school_code(school.code)
        except ValidationError as exc:
            messages.error(
                request,
                exc.messages[0] if getattr(exc, "messages", None) else str(exc),
            )
            return render(request, "admin/school_form.html", {"form": form, "title": "Create School"})
        if not school.saas_plan_id:
            school.saas_plan = Plan.objects.filter(name="Starter").first()
        _sync_school_subscription_from_saas(school)
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
        school = form.save(commit=False)
        _sync_school_subscription_from_saas(school)
        school.save()
        return redirect("admin_manage:schools_list")
    return render(request, "admin/school_form.html", {"form": form, "school": school, "title": "Edit School"})


@superadmin_required
@require_POST
def admin_school_delete(request, school_code):
    """
    Permanently remove a tenant school: public-schema row, related platform records (CASCADE),
    and the PostgreSQL schema (DROP … CASCADE) via TenantMixin.delete(force_drop=True).

    Requires typing DELETE in the confirmation form — never a one-click delete.
    """
    public = get_public_schema_name()
    with schema_context(public):
        school = get_object_or_404(School, code=school_code)
        if school.schema_name == public:
            messages.error(request, "The platform public tenant cannot be deleted.")
            return redirect("admin_manage:schools_list")

        phrase = (request.POST.get("confirmation_phrase") or "").strip().upper()
        if phrase != "DELETE":
            messages.error(
                request,
                "Deletion cancelled: type DELETE in the confirmation box to permanently remove a school.",
            )
            return redirect("admin_manage:schools_list")

        name, code = school.name, school.code
        try:
            school.delete(force_drop=True)
        except Exception as exc:
            messages.error(
                request,
                f"Could not delete school {code}: {exc}",
            )
            return redirect("admin_manage:schools_list")

    messages.success(
        request,
        f"School “{name}” ({code}) was deleted successfully, including its database schema and related platform data.",
    )
    return redirect("admin_manage:schools_list")


@superadmin_required
def admin_teachers_list(request):
    """Overview: totals + school cards. Detail: ?school=<code> with filters and paginated table."""
    from django_tenants.utils import tenant_context

    school_code = request.GET.get("school", "").strip()
    schools = School.objects.exclude(schema_name="public").order_by("name")

    if not school_code:
        cards, total_teachers, total_students = _platform_school_teacher_student_cards()
        return render(
            request,
            "admin/teachers_list.html",
            {
                "overview": True,
                "school_cards": cards,
                "total_teachers": total_teachers,
                "total_students": total_students,
                "schools": schools,
                "school_code": "",
                "filter_query": _filter_query(request),
            },
        )

    school = School.objects.filter(code=school_code).exclude(schema_name="public").first()
    if not school:
        messages.error(request, "School not found.")
        return redirect("admin_manage:teachers_list")

    search = request.GET.get("q", "").strip()
    subject_id = request.GET.get("subject", "").strip()
    status = request.GET.get("status", "").strip()

    try:
        with tenant_context(school):
            qs = (
                Teacher.objects.select_related("user", "user__school", "subject")
                .prefetch_related("subjects")
                .order_by("user__first_name", "user__last_name")
            )
            if search:
                qs = qs.filter(
                    Q(user__first_name__icontains=search)
                    | Q(user__last_name__icontains=search)
                    | Q(user__email__icontains=search)
                    | Q(user__username__icontains=search)
                    | Q(employee_id__icontains=search)
                )
            if subject_id.isdigit():
                sid = int(subject_id)
                qs = qs.filter(Q(subjects__id=sid) | Q(subject_id=sid)).distinct()
            if status == "active":
                qs = qs.filter(user__is_active=True)
            elif status == "inactive":
                qs = qs.filter(user__is_active=False)

            qs = qs.annotate(assignments_count=Count("class_section_subject_teacher_mappings", distinct=True))

            subject_choices = list(Subject.objects.order_by("name").values_list("id", "name"))

            paginator = Paginator(qs, 20)
            _ = paginator.count  # cache total COUNT while tenant schema is active
            page = _materialize_page_object_list(
                paginator.get_page(request.GET.get("page", 1))
            )
    except DatabaseError:
        _rollback_safe()
        messages.error(
            request,
            f"Could not load teachers for {school.name}: tenant schema may be missing tables. "
            "Run migrations for this school’s schema.",
        )
        return redirect("admin_manage:teachers_list")

    return render(
        request,
        "admin/teachers_list.html",
        {
            "overview": False,
            "teachers": page,
            "search": search,
            "subject_filter": subject_id,
            "status_filter": status,
            "subject_choices": subject_choices,
            "school_code": school_code,
            "school_obj": school,
            "schools": schools,
            "filter_query": _filter_query(request),
        },
    )


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
        return redirect(f"{reverse('admin_manage:teachers_list')}?school={school.code}")
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
        return redirect(f"{reverse('admin_manage:teachers_list')}?school={school.code}")
    return render(request, "admin/teacher_form.html", {"form": form, "teacher": teacher, "title": "Edit Teacher"})


@superadmin_required
def admin_students_list(request):
    """Overview: totals + school cards. Detail: ?school=<code> with filters and paginated table."""
    from django_tenants.utils import tenant_context

    school_code = request.GET.get("school", "").strip()
    schools = School.objects.exclude(schema_name="public").order_by("name")

    if not school_code:
        cards, total_teachers, total_students = _platform_school_teacher_student_cards()
        return render(
            request,
            "admin/students_list.html",
            {
                "overview": True,
                "school_cards": cards,
                "total_teachers": total_teachers,
                "total_students": total_students,
                "schools": schools,
                "school_code": "",
                "filter_query": _filter_query(request),
            },
        )

    school = School.objects.filter(code=school_code).exclude(schema_name="public").first()
    if not school:
        messages.error(request, "School not found.")
        return redirect("admin_manage:students_list")

    search = request.GET.get("q", "").strip()
    classroom_id = request.GET.get("classroom", "").strip()
    section_id = request.GET.get("section", "").strip()
    gender = request.GET.get("gender", "").strip()
    status = request.GET.get("status", "").strip()

    try:
        with tenant_context(school):
            qs = Student.objects.select_related(
                "user", "user__school", "classroom", "section", "academic_year"
            ).order_by("user__first_name", "user__last_name")
            if search:
                qs = qs.filter(
                    Q(user__first_name__icontains=search)
                    | Q(user__last_name__icontains=search)
                    | Q(admission_number__icontains=search)
                    | Q(roll_number__icontains=search)
                    | Q(phone__icontains=search)
                    | Q(parent_phone__icontains=search)
                )
            if classroom_id.isdigit():
                qs = qs.filter(classroom_id=int(classroom_id))
            if section_id.isdigit():
                qs = qs.filter(section_id=int(section_id))
            if gender in ("M", "F", "O"):
                qs = qs.filter(gender=gender)
            if status == "active":
                qs = qs.filter(user__is_active=True)
            elif status == "inactive":
                qs = qs.filter(user__is_active=False)

            classroom_choices = list(ClassRoom.objects.order_by("name").values_list("id", "name"))
            section_choices = list(Section.objects.order_by("name").values_list("id", "name"))

            paginator = Paginator(qs, 20)
            _ = paginator.count  # cache total COUNT while tenant schema is active
            page = _materialize_page_object_list(
                paginator.get_page(request.GET.get("page", 1))
            )
    except DatabaseError:
        _rollback_safe()
        messages.error(
            request,
            f"Could not load students for {school.name}: tenant schema may be missing tables. "
            "Run migrations for this school’s schema.",
        )
        return redirect("admin_manage:students_list")

    return render(
        request,
        "admin/students_list.html",
        {
            "overview": False,
            "students": page,
            "search": search,
            "classroom_filter": classroom_id,
            "section_filter": section_id,
            "gender_filter": gender,
            "status_filter": status,
            "classroom_choices": classroom_choices,
            "section_choices": section_choices,
            "school_code": school_code,
            "school_obj": school,
            "schools": schools,
            "filter_query": _filter_query(request),
        },
    )


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
        return redirect(f"{reverse('admin_manage:students_list')}?school={school.code}")
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
        return redirect(f"{reverse('admin_manage:students_list')}?school={school.code}")
    return render(request, "admin/student_form.html", {"form": form, "student": student, "title": "Edit Student"})


# ======================
# School Plans (Starter / Enterprise)
# ======================


@superadmin_required
def admin_school_plans_list(request):
    """School Plans section: list schools with current plan and enabled features."""
    search_q = request.GET.get("q", "").strip()
    plan_filter = request.GET.get("plan", "").strip()

    schools = (
        School.objects.exclude(schema_name="public")
        .select_related("saas_plan")
        .prefetch_related(
            "saas_plan__features",
            Prefetch(
                "subscription_records",
                queryset=SchoolSubscription.objects.filter(is_current=True).select_related(
                    "plan", "coupon"
                ),
                to_attr="current_subscription_rows",
            ),
        )
    )
    if search_q:
        schools = schools.filter(
            Q(name__icontains=search_q) | Q(code__icontains=search_q)
        )
    if plan_filter == "none":
        schools = schools.filter(saas_plan__isnull=True)
    elif plan_filter.isdigit():
        schools = schools.filter(saas_plan_id=int(plan_filter))

    schools = schools.order_by("name")
    plans = Plan.sale_tiers().prefetch_related("features")
    sale_plan_ids = list(plans.values_list("pk", flat=True))
    return render(
        request,
        "admin/school_plans_list.html",
        {
            "schools": schools,
            "plans": plans,
            "search_q": search_q,
            "plan_filter": plan_filter,
            "sale_plan_ids": sale_plan_ids,
        },
    )


@superadmin_required
def admin_school_change_plan(request, school_code):
    """Change a school's SaaS plan; optional coupon and subscription audit row (one current per school)."""
    from apps.customers.billing_coupons import coupon_error_message, redeem_coupon_for_subscription

    school = get_object_or_404(School, code=school_code)
    plans = Plan.sale_tiers().prefetch_related("features")
    current_list = list(
        SchoolSubscription.objects.filter(school=school, is_current=True).select_related("plan", "coupon")
    )
    current_sub = current_list[0] if current_list else None

    if request.method == "POST":
        plan_id = request.POST.get("plan")
        if plan_id:
            plan = get_object_or_404(Plan.sale_tiers(), pk=plan_id)
            start_d = _parse_optional_date(request.POST.get("start_date") or "") or date.today()
            end_d = _parse_optional_date(request.POST.get("end_date") or "")
            try:
                students_count = max(0, int(request.POST.get("students_count") or 0))
            except ValueError:
                students_count = 0
            try:
                free_months = min(120, max(0, int(request.POST.get("free_months") or 0)))
            except ValueError:
                free_months = 0
            sub_status = (request.POST.get("subscription_status") or "").strip()
            if sub_status not in {c[0] for c in SchoolSubscription.Status.choices}:
                sub_status = SchoolSubscription.Status.ACTIVE
            coupon_code = (request.POST.get("coupon_code") or "").strip().upper()

            if coupon_code:
                c0 = Coupon.objects.filter(code__iexact=coupon_code).first()
                err_pre = coupon_error_message(c0, code=coupon_code)
                if err_pre:
                    messages.error(request, err_pre)
                    return render(
                        request,
                        "admin/school_change_plan.html",
                        {
                            "school": school,
                            "plans": plans,
                            "current_sub": current_sub,
                            "today": date.today(),
                            "subscription_status_choices": SchoolSubscription.Status.choices,
                        },
                    )

            coupon_locked = None
            try:
                with transaction.atomic():
                    if coupon_code:
                        coupon_locked = Coupon.objects.select_for_update().get(code__iexact=coupon_code)
                        err2 = coupon_error_message(coupon_locked, code=coupon_code)
                        if err2:
                            raise ValueError(err2)
                        redeem_coupon_for_subscription(coupon_locked)
                    SchoolSubscription.objects.filter(school=school, is_current=True).update(is_current=False)
                    SchoolSubscription.objects.create(
                        school=school,
                        plan=plan,
                        start_date=start_d,
                        end_date=end_d,
                        students_count=students_count,
                        coupon=coupon_locked,
                        free_months_applied=free_months,
                        status=sub_status,
                        is_current=True,
                    )
                    school.saas_plan = plan
                    school.enabled_features_override = None
                    _sync_school_subscription_from_saas(school)
                    school.save()
            except (ValueError, Coupon.DoesNotExist) as e:
                messages.error(request, str(e) if str(e) else "Coupon could not be applied.")
                return render(
                    request,
                    "admin/school_change_plan.html",
                    {
                        "school": school,
                        "plans": plans,
                        "current_sub": current_sub,
                        "today": date.today(),
                        "subscription_status_choices": SchoolSubscription.Status.choices,
                    },
                )
            msg = f"Plan updated to {plan.name}."
            if coupon_locked:
                msg += f" Coupon {coupon_locked.code} applied."
            messages.success(request, msg)
        else:
            with transaction.atomic():
                SchoolSubscription.objects.filter(school=school, is_current=True).update(is_current=False)
                school.saas_plan = None
                school.enabled_features_override = None
                school.save()
            messages.success(
                request,
                "Plan cleared. Assign Starter or Enterprise under School Plans so modules are available.",
            )
        return redirect("admin_manage:school_plans_list")
    return render(
        request,
        "admin/school_change_plan.html",
        {
            "school": school,
            "plans": plans,
            "current_sub": current_sub,
            "today": date.today(),
            "subscription_status_choices": SchoolSubscription.Status.choices,
        },
    )


@superadmin_required
def admin_billing_plans_list(request):
    """Subscription product plans (customers.Plan): pricing, billing cycle, active flag."""
    plan_qs = Plan.objects.prefetch_related("features").order_by("price_per_student", "name")
    return render(request, "admin/billing_plans_list.html", {"plans": plan_qs})


@superadmin_required
def admin_coupons_list(request):
    coupons = Coupon.objects.all().order_by("-created_at")
    return render(request, "admin/coupons_list.html", {"coupons": coupons})


@superadmin_required
def admin_coupon_create(request):
    form = AdminCouponForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, "Coupon created.")
        return redirect("admin_manage:coupons_list")
    return render(request, "admin/coupon_form.html", {"form": form, "title": "Create coupon"})


@superadmin_required
def admin_coupon_edit(request, pk: int):
    coupon = get_object_or_404(Coupon, pk=pk)
    form = AdminCouponForm(request.POST or None, instance=coupon)
    if form.is_valid():
        form.save()
        messages.success(request, "Coupon updated.")
        return redirect("admin_manage:coupons_list")
    return render(request, "admin/coupon_form.html", {"form": form, "coupon": coupon, "title": "Edit coupon"})


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
