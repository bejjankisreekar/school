from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.core.files.storage import default_storage
from django.http import Http404, HttpResponse, JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_GET, require_POST
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from datetime import date, timedelta
from decimal import Decimal
from calendar import monthrange
from io import BytesIO
import json
from urllib.parse import urlencode
from django.utils import timezone

from django.db import connection, transaction
from django.db.models import Count, F, Max, Min, Prefetch, Q, Sum
from django.db.models.expressions import RawSQL
from django.core.paginator import Paginator
from django.db.utils import DatabaseError, InternalError, OperationalError, ProgrammingError
from apps.customers.billing_engine import (
    ensure_invoice_for_period,
    record_invoice_payment,
    total_paid_for_invoice,
)
from apps.customers.models import (
    PlatformBillingReceipt,
    PlatformInvoice,
    PlatformInvoicePayment,
    SaaSPlatformPayment,
    School,
)
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
    ExamSession,
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
    StudentPromotion,
    StudentEnrollment,
)
User = get_user_model()
from .utils import (
    add_warning_once,
    has_feature_access,
    get_current_academic_year,
    get_current_academic_year_bounds,
    get_active_academic_year_obj,
    apply_active_year_filter,
    tenant_migrate_cli_hint,
)
from .forms import (
    ContactEnquiryForm,
    SaaSPlatformPaymentForm,
    SchoolEnrollmentSignupForm,
    SuperAdminEnrollmentDeclineForm,
    SuperAdminEnrollmentProvisionForm,
)
from .models import ContactEnquiry, SchoolEnrollmentRequest
from apps.accounts.decorators import (
    admin_required,
    superadmin_required,
    student_required,
    teacher_required,
    parent_required,
    feature_required,
)
from apps.core.pdf_utils import pdf_response, render_pdf_bytes

# ======================
# Public Pages
# ======================

def home(request):
    return render(request, "marketing/home.html")


def pricing(request):
    return render(request, "marketing/pricing.html")


def about(request):
    return render(request, "marketing/about.html")


def school_enrollment_signup(request):
    """Public signup: stores a pending enrollment for super admin to provision a tenant schema."""
    success = request.GET.get("success") == "1"
    if request.method == "POST":
        form = SchoolEnrollmentSignupForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except ProgrammingError:
                form.add_error(
                    None,
                    "Enrollment storage is not initialized. Run: python manage.py ensure_school_enrollment_table",
                )
            else:
                return redirect(f"{reverse('core:school_enroll')}?success=1")
    else:
        form = SchoolEnrollmentSignupForm()
    return render(request, "marketing/enroll.html", {"form": form, "success": success})


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


@superadmin_required
def superadmin_enrollments(request):
    """List school enrollment requests (public signups)."""
    status = (request.GET.get("status") or "pending").lower()
    qs = SchoolEnrollmentRequest.objects.select_related("school", "reviewed_by").order_by("-created_at")
    if status == "pending":
        qs = qs.filter(status=SchoolEnrollmentRequest.Status.PENDING)
    elif status == "provisioned":
        qs = qs.filter(status=SchoolEnrollmentRequest.Status.PROVISIONED)
    elif status == "declined":
        qs = qs.filter(status=SchoolEnrollmentRequest.Status.DECLINED)
    else:
        status = "all"
    return render(
        request,
        "superadmin/enrollments.html",
        {"enrollments": qs, "status": status},
    )


@transaction.non_atomic_requests
@superadmin_required
def superadmin_enrollment_detail(request, pk: int):
    """
    Review one enrollment. POST: create tenant (schema + migrations) or decline.
    non_atomic_requests: School.save() runs migrate_schemas and must not run inside ATOMIC_REQUESTS.
    """
    from django.contrib import messages

    from apps.customers.models import Plan as CustomerPlan, SubscriptionPlan

    from .tenant_provisioning import (
        mark_enrollment_declined,
        mark_enrollment_provisioned,
        provision_school_from_enrollment,
    )

    enrollment = get_object_or_404(SchoolEnrollmentRequest, pk=pk)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()
        if enrollment.status != SchoolEnrollmentRequest.Status.PENDING:
            messages.warning(request, "This enrollment was already processed.")
            return redirect("core:superadmin_enrollments")

        if action == "decline":
            df = SuperAdminEnrollmentDeclineForm(request.POST)
            if df.is_valid():
                mark_enrollment_declined(enrollment, request.user, df.cleaned_data.get("reason") or "")
                messages.success(request, "Enrollment declined.")
            return redirect("core:superadmin_enrollments")

        if action == "provision":
            pf = SuperAdminEnrollmentProvisionForm(request.POST)
            if not pf.is_valid():
                messages.error(request, "Invalid form.")
            else:
                tier = (pf.cleaned_data.get("billing_tier") or "trial").lower()
                # Accept legacy POST values if an old form is cached
                if tier in ("core",):
                    tier = "starter"
                if tier in ("advance",):
                    tier = "enterprise"
                trial_sp = SubscriptionPlan.objects.filter(name__iexact="trial", is_active=True).first()
                basic_sp = SubscriptionPlan.objects.filter(name__iexact="basic", is_active=True).first()
                pro_sp = SubscriptionPlan.objects.filter(name__iexact="pro", is_active=True).first()
                starter_p = CustomerPlan.objects.filter(name="Starter").first()
                enterprise_p = CustomerPlan.objects.filter(name="Enterprise").first()
                if tier == "enterprise":
                    sub = pro_sp or basic_sp or trial_sp
                    saas = enterprise_p
                elif tier == "starter":
                    sub = basic_sp or trial_sp
                    saas = starter_p
                else:
                    sub = trial_sp or basic_sp
                    saas = starter_p
                if sub is None:
                    messages.error(
                        request,
                        "No billing rows found. Run: python manage.py seed_subscription_plans",
                    )
                    return redirect("core:superadmin_enrollments")
                try:
                    connection.set_schema_to_public()
                    school = provision_school_from_enrollment(
                        institution_name=enrollment.institution_name,
                        contact_email=enrollment.email,
                        phone=enrollment.phone or "",
                        address_notes=enrollment.notes or "",
                        subscription_plan=sub,
                        saas_plan=saas,
                    )
                    mark_enrollment_provisioned(enrollment, school, request.user)
                    messages.success(
                        request,
                        f"Tenant created: {school.name} — schema “{school.schema_name}”. "
                        "Assign an admin user to this school in Django admin or Schools.",
                    )
                except Exception as exc:
                    messages.error(request, f"Provisioning failed: {exc}")
            return redirect("core:superadmin_enrollments")

    provision_form = SuperAdminEnrollmentProvisionForm()
    decline_form = SuperAdminEnrollmentDeclineForm()
    return render(
        request,
        "superadmin/enrollment_detail.html",
        {
            "enrollment": enrollment,
            "provision_form": provision_form,
            "decline_form": decline_form,
        },
    )


# ======================
# Super Admin Dashboard
# ======================

@transaction.non_atomic_requests
@superadmin_required
def super_admin_dashboard(request):
    from apps.customers.models import Plan

    from .platform_financials import build_super_admin_platform_snapshot, summarize_billing_rows

    snap = build_super_admin_platform_snapshot()
    billing_summary = summarize_billing_rows(snap["billing_rows"])
    plans = Plan.sale_tiers().prefetch_related("features")
    pending_enrollments = SchoolEnrollmentRequest.objects.filter(
        status=SchoolEnrollmentRequest.Status.PENDING
    ).count()
    return render(
        request,
        "core/dashboards/super_admin_dashboard.html",
        {
            "total_schools": snap["total_schools"],
            "total_teachers": snap["total_teachers"],
            "total_students": snap["total_students"],
            "total_classes": snap["total_classes"],
            "plans": plans,
            "pending_enrollments": pending_enrollments,
            "billing_summary": billing_summary,
        },
    )


@transaction.non_atomic_requests
@superadmin_required
def superadmin_platform_footprint(request):
    """Per-school and class/section teacher & student counts for verification."""
    from .platform_footprint import build_class_section_footprint, build_footprint_school_rows

    q = (request.GET.get("q") or "").strip()
    mode = (request.GET.get("mode") or "all").lower()
    if mode not in ("all", "students", "teachers"):
        mode = "all"

    raw_school = request.GET.get("school")
    school = None
    class_rows: list[dict] = []
    if raw_school:
        try:
            school = School.objects.exclude(schema_name="public").get(pk=int(raw_school))
            class_rows = build_class_section_footprint(school)
        except (ValueError, School.DoesNotExist):
            school = None

    # Platform-wide totals always include every tenant; table rows respect search `q`.
    total_teachers, total_students, total_classes, _ = build_footprint_school_rows(q=None)
    _, _, _, school_rows = build_footprint_school_rows(q=q or None)

    school_options = School.objects.exclude(schema_name="public").order_by("name")

    return render(
        request,
        "superadmin/platform_footprint.html",
        {
            "total_teachers": total_teachers,
            "total_students": total_students,
            "total_classes": total_classes,
            "school_rows": school_rows,
            "school": school,
            "class_rows": class_rows,
            "search_q": q,
            "mode": mode,
            "school_options": school_options,
        },
    )


@transaction.non_atomic_requests
@superadmin_required
def superadmin_school_financial(request, school_id: int):
    """Single-school billing dashboard: invoices, payments, subscription, charts."""
    from .school_financial_profile import build_school_financial_profile

    school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "generate_invoice":
            try:
                y = int(request.POST.get("year", 0))
                m = int(request.POST.get("month", 0))
                if 1 <= m <= 12 and 2020 <= y <= 2036:
                    ensure_invoice_for_period(school, y, m)
                    messages.success(request, f"Invoice for {m:02d}/{y} generated or refreshed.")
                else:
                    messages.error(request, "Invalid month or year.")
            except (TypeError, ValueError) as exc:
                messages.error(request, str(exc))
            return redirect("core:superadmin_school_financial", school_id=school.pk)

    invoice_status = (request.GET.get("status") or "").strip()
    date_from = None
    date_to = None
    try:
        if request.GET.get("date_from"):
            date_from = date.fromisoformat(request.GET["date_from"])
        if request.GET.get("date_to"):
            date_to = date.fromisoformat(request.GET["date_to"])
    except ValueError:
        date_from = date_to = None

    ctx = build_school_financial_profile(
        school,
        invoice_status=invoice_status or None,
        date_from=date_from,
        date_to=date_to,
    )
    ctx["school_id"] = school.pk
    ctx["month_choices_gen"] = [
        (1, "Jan"),
        (2, "Feb"),
        (3, "Mar"),
        (4, "Apr"),
        (5, "May"),
        (6, "Jun"),
        (7, "Jul"),
        (8, "Aug"),
        (9, "Sep"),
        (10, "Oct"),
        (11, "Nov"),
        (12, "Dec"),
    ]
    return render(request, "superadmin/school_financial_detail.html", ctx)


@superadmin_required
def superadmin_platform_invoice_pdf(request, invoice_id: int):
    """PDF for a platform SaaS invoice (superadmin)."""
    inv = get_object_or_404(PlatformInvoice.objects.select_related("school", "subscription"), pk=invoice_id)
    paid = total_paid_for_invoice(inv)
    remaining = (inv.final_amount - paid).quantize(Decimal("0.01"))
    pdf_bytes = render_pdf_bytes(
        "pdf/saas_platform_invoice.html",
        {
            "invoice": inv,
            "school": inv.school,
            "paid": paid,
            "remaining": remaining,
            "generated_on": timezone.now(),
        },
    )
    if not pdf_bytes:
        messages.error(request, "Could not generate PDF.")
        return redirect("core:superadmin_school_financial", school_id=inv.school_id)
    safe_name = f"{inv.invoice_number}.pdf".replace(" ", "_")
    return pdf_response(pdf_bytes, safe_name)


@transaction.non_atomic_requests
@superadmin_required
def superadmin_global_students(request):
    """All students across tenants with filters, fee columns, server-side pagination."""
    from .global_directory import (
        collect_global_students,
        platform_student_totals,
        school_filter_choices,
        sort_student_rows,
        tenant_dropdowns_for_school,
    )

    school_id = _parse_optional_int(request.GET.get("school"))
    classroom_id = _parse_optional_int(request.GET.get("classroom"))
    section_id = _parse_optional_int(request.GET.get("section"))
    academic_year_name = (request.GET.get("ay") or "").strip()
    status = (request.GET.get("status") or "").strip()
    fee_filter = (request.GET.get("fee") or "").strip()
    search = (request.GET.get("q") or "").strip()
    sort = (request.GET.get("sort") or "name").strip()
    if sort not in ("name", "school", "class", "fee_pending"):
        sort = "name"

    platform_total, platform_active, platform_inactive = platform_student_totals()

    rows = collect_global_students(
        school_id=school_id,
        classroom_id=classroom_id,
        section_id=section_id,
        academic_year_name=academic_year_name,
        status=status,
        search=search,
        fee_filter=fee_filter,
        today=date.today(),
    )
    sort_student_rows(rows, sort)

    filtered_count = len(rows)
    paginator = Paginator(rows, 25)
    page = paginator.get_page(request.GET.get("page", 1))

    tenant_dd = None
    selected_school = None
    if school_id:
        selected_school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
        tenant_dd = tenant_dropdowns_for_school(selected_school)

    fq = request.GET.copy()
    fq.pop("page", None)
    filter_query = fq.urlencode()

    return render(
        request,
        "superadmin/global_students.html",
        {
            "page_obj": page,
            "schools_qs": school_filter_choices(),
            "selected_school_id": school_id,
            "selected_school": selected_school,
            "tenant_dd": tenant_dd,
            "classroom_id": classroom_id,
            "section_id": section_id,
            "academic_year_name": academic_year_name,
            "status": status,
            "fee_filter": fee_filter,
            "search_q": search,
            "sort": sort,
            "platform_total": platform_total,
            "platform_active": platform_active,
            "platform_inactive": platform_inactive,
            "filtered_count": filtered_count,
            "filter_query": filter_query,
        },
    )


@transaction.non_atomic_requests
@superadmin_required
def superadmin_global_teachers(request):
    """All teachers across tenants with filters and pagination."""
    from .global_directory import (
        collect_global_teachers,
        platform_teacher_totals,
        school_filter_choices,
        sort_teacher_rows,
    )

    school_id = _parse_optional_int(request.GET.get("school"))
    subject_q = (request.GET.get("subject") or "").strip()
    status = (request.GET.get("status") or "").strip()
    search = (request.GET.get("q") or "").strip()
    sort = (request.GET.get("sort") or "name").strip()
    if sort not in ("name", "school", "classes"):
        sort = "name"

    platform_total, platform_active, platform_inactive = platform_teacher_totals()

    rows = collect_global_teachers(
        school_id=school_id,
        subject_q=subject_q,
        status=status,
        search=search,
    )
    sort_teacher_rows(rows, sort)

    filtered_count = len(rows)
    paginator = Paginator(rows, 25)
    page = paginator.get_page(request.GET.get("page", 1))

    fq = request.GET.copy()
    fq.pop("page", None)
    filter_query = fq.urlencode()

    return render(
        request,
        "superadmin/global_teachers.html",
        {
            "page_obj": page,
            "schools_qs": school_filter_choices(),
            "selected_school_id": school_id,
            "subject_q": subject_q,
            "status": status,
            "search_q": search,
            "sort": sort,
            "platform_total": platform_total,
            "platform_active": platform_active,
            "platform_inactive": platform_inactive,
            "filtered_count": filtered_count,
            "filter_query": filter_query,
        },
    )


def _parse_optional_int(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@transaction.non_atomic_requests
@superadmin_required
def superadmin_financials(request):
    """Platform subscription / trial status per school — not school-internal fee collection."""
    from .platform_financials import build_super_admin_platform_snapshot, summarize_billing_rows

    snap = build_super_admin_platform_snapshot()
    rows = snap["billing_rows"]
    summary = summarize_billing_rows(rows)
    return render(
        request,
        "superadmin/financials.html",
        {
            "billing_rows": rows,
            "billing_summary": summary,
        },
    )


@superadmin_required
def superadmin_subscription_payments(request):
    """Ledger of SaaS subscription money received from schools (platform operator)."""
    qs = SaaSPlatformPayment.objects.select_related(
        "school", "recorded_by", "subscription", "subscription__plan"
    ).order_by("-payment_date", "-id")
    paginator = Paginator(qs, 40)
    page = paginator.get_page(request.GET.get("page", 1))
    today = date.today()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    month_total = SaaSPlatformPayment.objects.filter(
        payment_date__gte=month_start,
        payment_date__lte=today,
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    year_total = SaaSPlatformPayment.objects.filter(
        payment_date__gte=year_start,
        payment_date__lte=today,
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    all_total = SaaSPlatformPayment.objects.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    return render(
        request,
        "superadmin/subscription_payments.html",
        {
            "payments": page,
            "month_total": month_total,
            "year_total": year_total,
            "all_total": all_total,
        },
    )


@superadmin_required
def superadmin_record_subscription_payment(request):
    """Record a payment received from a school (UPI, bank, cash, etc.)."""
    if request.method == "POST":
        form = SaaSPlatformPaymentForm(request.POST)
    else:
        initial = {"payment_date": date.today()}
        raw_school = request.GET.get("school")
        if raw_school:
            try:
                initial["school"] = School.objects.get(pk=int(raw_school))
            except (ValueError, School.DoesNotExist):
                pass
        form = SaaSPlatformPaymentForm(initial=initial)
    if request.method == "POST" and form.is_valid():
        pay = form.save(commit=False)
        pay.recorded_by = request.user
        pay.save()
        receipt_bit = f" — receipt {pay.internal_receipt_no}" if pay.internal_receipt_no else ""
        messages.success(
            request,
            f"Recorded ₹{pay.amount} from {pay.school.name} ({pay.get_payment_method_display()}){receipt_bit}.",
        )
        return redirect("core:superadmin_subscription_payments")
    return render(request, "superadmin/record_subscription_payment.html", {"form": form})


@superadmin_required
def superadmin_edit_subscription_payment(request, pk: int):
    """Update a logged SaaS subscription payment (superadmin). Totals elsewhere use live DB queries."""
    pay = get_object_or_404(SaaSPlatformPayment, pk=pk)
    if request.method == "POST":
        form = SaaSPlatformPaymentForm(request.POST, instance=pay)
        if form.is_valid():
            saved = form.save()
            messages.success(
                request,
                f"Updated payment ₹{saved.amount} for {saved.school.name} ({saved.payment_date}).",
            )
            return redirect("core:superadmin_subscription_payments")
    else:
        form = SaaSPlatformPaymentForm(instance=pay)
    return render(
        request,
        "superadmin/record_subscription_payment.html",
        {"form": form, "payment": pay},
    )


@superadmin_required
@require_POST
def superadmin_delete_subscription_payment(request, pk: int):
    """Remove a subscription payment log entry; aggregates on Financials / billing use DB sums."""
    pay = get_object_or_404(SaaSPlatformPayment, pk=pk)
    summary = f"{pay.school.name} — ₹{pay.amount} on {pay.payment_date}"
    pay.delete()
    messages.success(request, f"Removed payment log: {summary}.")
    return redirect("core:superadmin_subscription_payments")


@superadmin_required
def superadmin_billing_invoices(request):
    """Monthly SaaS invoices (platform billing engine) and period generator."""
    today = date.today()
    try:
        y = int(request.GET.get("year", today.year))
        m = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        y, m = today.year, today.month
    y = max(2020, min(2036, y))
    m = max(1, min(12, m))

    if request.method == "POST" and request.POST.get("action") == "generate_period":
        schools = School.objects.exclude(schema_name="public").order_by("name")
        created = 0
        for school in schools:
            _, was_created = ensure_invoice_for_period(school, y, m)
            if was_created:
                created += 1
        messages.success(
            request,
            f"Invoices for {m:02d}/{y}: {created} new; others already existed or were skipped.",
        )
        return redirect(f"{reverse('core:superadmin_billing_invoices')}?year={y}&month={m}")

    invoices = (
        PlatformInvoice.objects.filter(year=y, month=m)
        .select_related("school", "subscription", "subscription__plan")
        .prefetch_related(
            Prefetch(
                "invoice_payments",
                queryset=PlatformInvoicePayment.objects.select_related(
                    "billing_receipt",
                ).order_by("-paid_on"),
            )
        )
        .order_by("school__name")
    )
    rows = []
    for inv in invoices:
        paid = total_paid_for_invoice(inv)
        rows.append(
            {
                "invoice": inv,
                "paid": paid,
                "remaining": (inv.final_amount - paid).quantize(Decimal("0.01")),
            }
        )

    month_choices = [
        (1, "January"),
        (2, "February"),
        (3, "March"),
        (4, "April"),
        (5, "May"),
        (6, "June"),
        (7, "July"),
        (8, "August"),
        (9, "September"),
        (10, "October"),
        (11, "November"),
        (12, "December"),
    ]
    return render(
        request,
        "superadmin/billing_invoices.html",
        {
            "year": y,
            "month": m,
            "rows": rows,
            "month_choices": month_choices,
            "year_options": range(2020, 2037),
        },
    )


@superadmin_required
def superadmin_billing_invoice_pay(request, invoice_id):
    """Record a payment against an invoice; creates receipt PDF."""
    inv = get_object_or_404(
        PlatformInvoice.objects.select_related("school", "subscription"),
        pk=invoice_id,
    )
    paid = total_paid_for_invoice(inv)
    remaining = (inv.final_amount - paid).quantize(Decimal("0.01"))

    if request.method == "POST":
        raw_amt = (request.POST.get("amount_paid") or "").strip()
        try:
            amount = Decimal(raw_amt)
        except Exception:
            amount = Decimal("0")
        mode = (request.POST.get("payment_mode") or "upi").lower()
        if mode not in ("upi", "cash", "bank"):
            mode = "upi"
        txn = (request.POST.get("transaction_id") or "").strip()
        if amount <= 0:
            messages.error(request, "Enter an amount greater than zero.")
        else:
            try:
                record_invoice_payment(
                    invoice=inv,
                    amount_paid=amount,
                    payment_mode=mode,
                    transaction_id=txn,
                    user=request.user,
                )
                messages.success(request, "Payment recorded. Receipt PDF generated.")
                return redirect(f"{reverse('core:superadmin_billing_invoices')}?year={inv.year}&month={inv.month}")
            except ValueError as exc:
                messages.error(request, str(exc))

    return render(
        request,
        "superadmin/billing_invoice_pay.html",
        {
            "invoice": inv,
            "paid_so_far": paid,
            "remaining": remaining,
        },
    )


@superadmin_required
def superadmin_billing_receipt_pdf(request, receipt_id):
    """Download stored receipt PDF for a platform billing payment."""
    receipt = get_object_or_404(
        PlatformBillingReceipt.objects.select_related("payment", "payment__school"),
        pk=receipt_id,
    )
    path = receipt.pdf_url
    if not path or not default_storage.exists(path):
        messages.error(request, "Receipt PDF not found. It may still be generating.")
        inv = receipt.payment.invoice
        return redirect(f"{reverse('core:superadmin_billing_invoices')}?year={inv.year}&month={inv.month}")
    with default_storage.open(path, "rb") as fh:
        data = fh.read()
    safe_name = f"{receipt.receipt_number}.pdf".replace(" ", "_")
    return pdf_response(data, safe_name)


@superadmin_required
def superadmin_billing_sales(request):
    """
    Sales-ready billing overview: amount due vs collected vs outstanding per school,
    with period filter and CSV export.
    """
    import csv

    from django.http import HttpResponse

    from .platform_billing_sales import build_billing_sales_rows, summarize_billing_sales

    today = date.today()
    try:
        y = int(request.GET.get("year", today.year))
        m = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        y, m = today.year, today.month
    y = max(2020, min(2036, y))
    m = max(1, min(12, m))

    billing_error = False
    try:
        rows = build_billing_sales_rows(y, m)
        summary = summarize_billing_sales(rows)
    except ProgrammingError:
        rows = []
        summary = {
            "total_monthly_due": Decimal("0"),
            "total_collected_month": Decimal("0"),
            "total_outstanding_month": Decimal("0"),
            "total_advance_month": Decimal("0"),
            "total_ytd": Decimal("0"),
            "total_all_time": Decimal("0"),
            "count_paying_schools": 0,
            "collection_rate_pct": None,
        }
        billing_error = True

    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="subscription_billing_{y}_{m:02d}.csv"'
        response.write("\ufeff")
        w = csv.writer(response)
        w.writerow(
            [
                "Code",
                "School",
                "Contact person",
                "Email",
                "Phone",
                "Plan",
                "Price per student (INR)",
                "Billing cycle",
                "Students",
                "Monthly amount due (INR)",
                "Status",
                f"Paid ({m:02d}/{y}) (INR)",
                "YTD paid (INR)",
                "All-time paid (INR)",
                "Outstanding month (INR)",
                "Advance / overpay (INR)",
                "Collection % (month)",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.code,
                    r.name,
                    r.contact_person,
                    r.contact_email,
                    r.phone,
                    r.plan_name,
                    r.price_per_student if r.price_per_student is not None else "",
                    r.billing_cycle,
                    r.student_count,
                    r.monthly_amount_due,
                    r.status_label,
                    r.paid_in_month,
                    r.paid_ytd,
                    r.paid_all_time,
                    r.outstanding_month,
                    r.advance_month,
                    r.collection_pct_month if r.collection_pct_month is not None else "",
                ]
            )
        return response

    if m == 1:
        prev_y, prev_m = y - 1, 12
    else:
        prev_y, prev_m = y, m - 1
    if m == 12:
        next_y, next_m = y + 1, 1
    else:
        next_y, next_m = y, m + 1

    month_label = date(y, m, 1).strftime("%B %Y")
    year_options = list(range(2020, 2038))

    return render(
        request,
        "superadmin/billing_sales.html",
        {
            "rows": rows,
            "summary": summary,
            "year": y,
            "month": m,
            "month_label": month_label,
            "year_options": year_options,
            "prev_y": prev_y,
            "prev_m": prev_m,
            "next_y": next_y,
            "next_m": next_m,
            "billing_error": billing_error,
        },
    )


# ======================
# School Admin Dashboard
# ======================

@admin_required
def admin_dashboard(request):
    school = request.user.school
    empty_ctx = {
        "current_plan": None,
        "plan_name": "",
        "plan_features": [],
        "trial_expired": False,
        "total_students": 0,
        "total_teachers": 0,
        "today_attendance_pct": 0,
        "staff_attendance_pct": 0,
        "fee_today_amt": 0,
        "fee_month_amt": 0,
        "pending_fees": 0,
        "has_fees": False,
        "has_attendance": False,
        "has_exams": False,
        "fees_enabled": False,
        "attendance_enabled": False,
        "exams_enabled": False,
        "total_classes": 0,
        "total_sections": 0,
        "total_subjects": 0,
        "active_academic_year_label": "",
        "upcoming_exams_count": 0,
        "upcoming_exams": [],
        "today_birthdays": [],
        "pending_attendance": [],
        "top_students": [],
        "class_distribution": [],
        "attendance_trend": [],
        "show_class_chart": False,
        "show_attendance_trend": False,
        "today_iso": date.today().isoformat(),
    }
    if not school:
        return render(request, "core/dashboards/admin_dashboard.html", empty_ctx)
    if school.is_trial_expired():
        return render(request, "core/dashboards/trial_expired.html", {"school": school})

    today = date.today()
    month_start = today.replace(day=1)
    week_end = today + timedelta(days=7)

    has_fees = has_feature_access(school, "fees", user=request.user)
    has_attendance = has_feature_access(school, "attendance", user=request.user)
    has_exams = has_feature_access(school, "exams", user=request.user)

    total_students = Student.objects.filter(user__school=school).count()
    total_teachers = Teacher.objects.filter(user__school=school).count()

    attendance_present = 0
    staff_today_present = 0
    if has_attendance:
        attendance_present = Attendance.objects.filter(
            date=today,
            status=Attendance.Status.PRESENT,
            student__user__school=school,
        ).count()
        staff_att_today = StaffAttendance.objects.filter(teacher__user__school=school, date=today)
        staff_today_present = staff_att_today.filter(status=StaffAttendance.Status.PRESENT).count()

    today_attendance_pct = (
        round((attendance_present / total_students * 100), 1) if total_students else 0
    )
    staff_attendance_pct = (
        round((staff_today_present / total_teachers * 100), 1) if total_teachers else 0
    )

    fee_today_amt = 0.0
    fee_month_amt = 0.0
    pending_fees = 0.0
    if has_fees:
        # Nested atomic = savepoint: if a query fails (e.g. schema drift), PostgreSQL
        # would otherwise abort the whole request transaction (ATOMIC_REQUESTS=True).
        try:
            with transaction.atomic():
                fee_today_amt = float(
                    Payment.objects.filter(
                        payment_date=today,
                        fee__student__user__school=school,
                    ).aggregate(s=Sum("amount"))["s"]
                    or 0
                )
                fee_month_amt = float(
                    Payment.objects.filter(
                        payment_date__gte=month_start,
                        payment_date__lte=today,
                        fee__student__user__school=school,
                    ).aggregate(s=Sum("amount"))["s"]
                    or 0
                )
                for fee in Fee.objects.filter(
                    status__in=["PENDING", "PARTIAL"],
                    student__user__school=school,
                ).prefetch_related("payments"):
                    paid = sum(float(p.amount) for p in fee.payments.all())
                    pending_fees += float(fee.amount) - paid
        except Exception:
            fee_today_amt = fee_month_amt = 0.0
            pending_fees = 0.0

    upcoming_exams = []
    if has_exams:
        upcoming_exams = list(
            _exam_read_qs()
            .filter(date__gte=today, date__lte=week_end)
            .order_by("date", "id")[:20]
        )
    upcoming_exams_count = len(upcoming_exams)

    total_classes = ClassRoom.objects.count()
    total_sections = Section.objects.count()
    total_subjects = Subject.objects.count()
    active_ay = get_active_academic_year_obj()
    active_academic_year_label = (
        active_ay.name.strip()
        if active_ay and getattr(active_ay, "name", None)
        else get_current_academic_year()
    )

    today_birthdays = list(
        Student.objects.filter(
            user__school=school,
            date_of_birth__isnull=False,
            date_of_birth__month=today.month,
            date_of_birth__day=today.day,
        )
        .select_related("user", "classroom", "section")
        .defer("academic_year")
    )

    pending_attendance = []
    if has_attendance and total_students:
        pair_rows = (
            Student.objects.filter(
                user__school=school,
                classroom__isnull=False,
                section__isnull=False,
            )
            .values("classroom_id", "section_id")
            .annotate(student_count=Count("id"))
        )
        for row in pair_rows:
            cid, sid = row["classroom_id"], row["section_id"]
            n = row["student_count"]
            marked = Attendance.objects.filter(
                date=today,
                student__classroom_id=cid,
                student__section_id=sid,
                student__user__school=school,
            ).count()
            if marked < n:
                pending_attendance.append(
                    {
                        "classroom_id": cid,
                        "section_id": sid,
                        "class_name": "",
                        "section_name": "",
                        "marked": marked,
                        "total": n,
                        "missing": n - marked,
                    }
                )
        if pending_attendance:
            cids = {p["classroom_id"] for p in pending_attendance}
            sids = {p["section_id"] for p in pending_attendance}
            cnames = dict(ClassRoom.objects.filter(pk__in=cids).values_list("id", "name"))
            snames = dict(Section.objects.filter(pk__in=sids).values_list("id", "name"))
            for p in pending_attendance:
                p["class_name"] = cnames.get(p["classroom_id"], "—")
                p["section_name"] = snames.get(p["section_id"], "—")
            pending_attendance.sort(
                key=lambda x: (-x["missing"], x["class_name"], x["section_name"])
            )

    top_students = []
    marks_exist = Marks.objects.filter(
        total_marks__gt=0,
        student__user__school=school,
    ).exists()
    if marks_exist:
        top_marks = (
            Marks.objects.filter(total_marks__gt=0, student__user__school=school)
            .annotate(pct=F("marks_obtained") * 100 / F("total_marks"))
            .select_related("student__user", "student__classroom", "student__section")
            .defer("student__academic_year")
            .order_by("-pct")[:15]
        )
        seen_students = set()
        for m in top_marks:
            if m.student_id in seen_students:
                continue
            seen_students.add(m.student_id)
            pct = round((m.marks_obtained * 100) / m.total_marks)
            top_students.append({"student": m.student, "percentage": pct})
            if len(top_students) >= 5:
                break

    class_dist_qs = (
        ClassRoom.objects.annotate(
            cnt=Count("students", filter=Q(students__user__school=school))
        )
        .values("name", "cnt")
        .order_by("name")
    )
    class_distribution = [{"label": x["name"], "count": x["cnt"]} for x in class_dist_qs if x["cnt"]]
    show_class_chart = bool(class_distribution)

    attendance_trend = []
    if has_attendance and total_students:
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            pres = Attendance.objects.filter(
                date=d,
                status=Attendance.Status.PRESENT,
                student__user__school=school,
            ).count()
            pct = round((pres / total_students * 100), 1) if total_students else 0
            attendance_trend.append(
                {
                    "label": d.strftime("%d %b"),
                    "short": d.strftime("%a"),
                    "iso": d.isoformat(),
                    "pct": pct,
                    "present": pres,
                }
            )
        show_attendance_trend = any(x["present"] or x["pct"] for x in attendance_trend)
    else:
        show_attendance_trend = False

    from apps.customers.subscription import PLAN_FEATURES

    saas = school.saas_plan
    if saas:
        current_plan = saas
        plan_name = (saas.name or "").lower()
        plan_features = list(saas.features.values_list("code", flat=True))
    else:
        sub = school.plan
        plan_name = (sub.name if sub else "").lower() or "basic"
        plan_features = PLAN_FEATURES.get(plan_name, [])
        current_plan = sub

    return render(
        request,
        "core/dashboards/admin_dashboard.html",
        {
            "total_students": total_students,
            "total_teachers": total_teachers,
            "today_attendance_pct": today_attendance_pct,
            "staff_attendance_pct": staff_attendance_pct,
            "fee_today_amt": fee_today_amt,
            "fee_month_amt": fee_month_amt,
            "pending_fees": pending_fees,
            "has_fees": has_fees,
            "has_attendance": has_attendance,
            "has_exams": has_exams,
            "fees_enabled": has_fees,
            "attendance_enabled": has_attendance,
            "exams_enabled": has_exams,
            "total_classes": total_classes,
            "total_sections": total_sections,
            "total_subjects": total_subjects,
            "active_academic_year_label": active_academic_year_label,
            "upcoming_exams_count": upcoming_exams_count,
            "upcoming_exams": upcoming_exams,
            "today_birthdays": today_birthdays,
            "pending_attendance": pending_attendance,
            "top_students": top_students,
            "class_distribution": class_distribution,
            "attendance_trend": attendance_trend,
            "show_class_chart": show_class_chart,
            "show_attendance_trend": show_attendance_trend,
            "today_iso": today.isoformat(),
            "current_plan": current_plan,
            "plan_name": plan_name,
            "plan_features": plan_features,
            "trial_expired": school.is_trial_expired(),
        },
    )


# ======================
# Teacher Dashboard
# ======================


def _homework_queryset_for_teacher(teacher, user):
    """
    Homework visible to a teacher: created/assigned to them, admin assignments
    matching any of their (class, section) teaching pairs, or legacy rows for
    subjects they teach anywhere in the school.
    """
    if not teacher:
        return Homework.objects.none()

    q = Q(teacher_id=teacher.pk) | Q(assigned_by_id=user.pk)

    pairs = list(
        ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
        .values_list("class_obj_id", "section_id")
        .distinct()
    )
    for cid, sid in pairs:
        q |= Q(classes__id=cid, sections__id=sid)

    subj_ids = list(
        ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
        .values_list("subject_id", flat=True)
        .distinct()
    )
    if subj_ids:
        q |= Q(subject_id__in=subj_ids)

    return (
        Homework.objects.filter(q)
        .distinct()
        .select_related("subject", "assigned_by", "teacher", "teacher__user")
        .prefetch_related("classes", "sections")
        .order_by("-due_date", "-created_at")
    )


def _teacher_assigned_subject_display(teacher):
    """
    Dashboard KPI: align with admin teacher list — use profile subjects, legacy
    subject FK, ClassSectionSubjectTeacher rows, then classroom M2M. Admins often
    assign only classes or only CSST rows, leaving subjects M2M empty; the old
    logic only read subjects M2M and showed 'Not assigned' incorrectly.
    """
    if not teacher:
        return "Not assigned"
    names = []
    seen = set()
    for s in teacher.subjects.all().order_by("name"):
        if s.id not in seen:
            seen.add(s.id)
            names.append(s.name)
    if getattr(teacher, "subject_id", None) and teacher.subject_id not in seen and teacher.subject:
        seen.add(teacher.subject_id)
        names.append(teacher.subject.name)
    mappings = list(teacher.class_section_subject_teacher_mappings.all())
    mappings.sort(
        key=lambda r: (
            (r.subject.name or "").lower(),
            (r.class_obj.name if r.class_obj else "") or "",
        )
    )
    for row in mappings:
        if row.subject_id not in seen:
            seen.add(row.subject_id)
            names.append(row.subject.name)
    if names:
        return ", ".join(names)
    classrooms = list(teacher.classrooms.all().order_by("name"))
    if classrooms:
        return "Classes: " + ", ".join(c.name for c in classrooms)
    return "Not assigned"


@teacher_required
def teacher_dashboard(request):
    teacher = getattr(request.user, "teacher_profile", None)
    if teacher:
        teacher = (
            Teacher.objects.filter(pk=teacher.pk)
            .select_related("subject", "user")
            .prefetch_related(
                "subjects",
                "classrooms",
                Prefetch(
                    "class_section_subject_teacher_mappings",
                    queryset=ClassSectionSubjectTeacher.objects.select_related(
                        "subject", "class_obj", "section"
                    ),
                ),
            )
            .first()
        )
    school = getattr(request.user, "school", None)
    assigned_subject = None
    if teacher:
        assigned_subject = teacher.subjects.first() or teacher.subject
        if assigned_subject is None:
            first_csst = (
                ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
                .select_related("subject")
                .order_by("subject__name")
                .first()
            )
            if first_csst:
                assigned_subject = first_csst.subject

    has_exams = bool(school and teacher and has_feature_access(school, "exams", user=request.user))
    upcoming_exams = []
    upcoming_exams_count = 0
    if has_exams and teacher:
        today = date.today()
        uq = (
            Exam.objects.filter(_teacher_visible_exam_q(teacher))
            .filter(date__gte=today)
            .select_related("session", "subject", "teacher__user")
            .order_by("date", "subject__name")
        )
        upcoming_exams_count = uq.count()
        upcoming_exams = list(uq[:8])

    has_timetable = bool(school and has_feature_access(school, "timetable", user=request.user))
    today_schedule = []
    if teacher and has_timetable:
        try:
            from apps.timetable.views import today_schedule_teacher

            today_schedule = today_schedule_teacher(teacher)
        except Exception:
            today_schedule = []

    has_homework_feature = bool(school and has_feature_access(school, "homework", user=request.user))
    homework_recent = []
    homework_total = 0
    if teacher and has_homework_feature:
        qs = _homework_queryset_for_teacher(teacher, request.user)
        homework_total = qs.count()
        homework_recent = list(qs[:8])

    assigned_subject_display = _teacher_assigned_subject_display(teacher)
    return render(
        request,
        "core/dashboards/teacher_dashboard.html",
        {
            "assigned_subject": assigned_subject,
            "assigned_subject_display": assigned_subject_display,
            "today_schedule": today_schedule,
            "today_schedule_count": len(today_schedule),
            "has_timetable": has_timetable,
            "has_homework_feature": has_homework_feature,
            "homework_recent": homework_recent,
            "homework_total": homework_total,
            "has_exams": has_exams,
            "upcoming_exams": upcoming_exams,
            "upcoming_exams_count": upcoming_exams_count,
        },
    )


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
    if not has_feature_access(getattr(request.user, "school", None), "attendance", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
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
    Build summary list for the student: one row per exam *session* (all subjects aggregated),
    plus one row per legacy standalone exam paper (no session).
    """
    exams = []
    exam_marks = (
        Marks.objects.filter(student=student, exam__isnull=False)
        .select_related("exam", "exam__session", "subject")
    )
    by_session = {}
    standalone_by_exam = {}
    for m in exam_marks:
        ex = m.exam
        if ex.session_id:
            sid = ex.session_id
            if sid not in by_session:
                by_session[sid] = {"session": ex.session, "marks": []}
            by_session[sid]["marks"].append(m)
        else:
            eid = ex.id
            if eid not in standalone_by_exam:
                standalone_by_exam[eid] = {"exam": ex, "marks": []}
            standalone_by_exam[eid]["marks"].append(m)

    for sid, data in by_session.items():
        marks = data["marks"]
        sess = data["session"]
        total_o = sum(x.marks_obtained for x in marks)
        total_m = sum(x.total_marks for x in marks)
        pct = round((total_o / total_m * 100) if total_m else 0, 1)
        paper_dates = [x.exam.date for x in marks if x.exam and x.exam.date]
        dmin = min(paper_dates) if paper_dates else None
        dmax = max(paper_dates) if paper_dates else None
        exams.append({
            "is_session": True,
            "session_id": sid,
            "session": sess,
            "exam": None,
            "exam_id": None,
            "exam_name": sess.name,
            "exam_date": dmax or dmin,
            "date_min": dmin,
            "date_max": dmax,
            "total_subjects": len({x.subject_id for x in marks}),
            "overall_pct": pct,
            "grade": _grade_from_pct(pct),
            "has_marks": total_m > 0,
        })

    for eid, data in standalone_by_exam.items():
        marks = data["marks"]
        ex = data["exam"]
        total_o = sum(x.marks_obtained for x in marks)
        total_m = sum(x.total_marks for x in marks)
        pct = round((total_o / total_m * 100) if total_m else 0, 1)
        exams.append({
            "is_session": False,
            "session_id": None,
            "session": None,
            "exam": ex,
            "exam_id": eid,
            "exam_name": ex.name,
            "exam_date": ex.date,
            "date_min": ex.date,
            "date_max": ex.date,
            "total_subjects": len(marks),
            "overall_pct": pct,
            "grade": _grade_from_pct(pct),
            "has_marks": total_m > 0,
        })

    # Scheduled exam sessions for this class–section (no marks yet)
    if student.classroom and student.section:
        cn = student.classroom.name
        sn = student.section.name
        from_marks_session_ids = {e["session_id"] for e in exams if e.get("is_session") and e.get("session_id")}
        scheduled = ExamSession.objects.filter(class_name__iexact=cn, section__iexact=sn).annotate(
            paper_count=Count("papers", distinct=True),
            dmin=Min("papers__date"),
            dmax=Max("papers__date"),
        ).filter(paper_count__gt=0)
        if from_marks_session_ids:
            scheduled = scheduled.exclude(id__in=from_marks_session_ids)
        for sess in scheduled:
            exams.append({
                "is_session": True,
                "session_id": sess.id,
                "session": sess,
                "exam": None,
                "exam_id": None,
                "exam_name": sess.name,
                "exam_date": sess.dmax or sess.dmin,
                "date_min": sess.dmin,
                "date_max": sess.dmax,
                "total_subjects": sess.paper_count,
                "overall_pct": None,
                "grade": "—",
                "has_marks": False,
            })

    exams.sort(key=lambda e: e["exam_date"] or date.min, reverse=True)
    return exams


@student_required
def student_marks(request):
    """
    Flat results table: Exam | Subject | Marks | Total | % — only for logged-in student.
    """
    school = getattr(request.user, "school", None)
    if not has_feature_access(school, "exams", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    student = getattr(request.user, "student_profile", None)
    if not student:
        return render(
            request,
            "core/student_dashboard/marks.html",
            {"mark_rows": [], "exam_summaries": []},
        )
    marks_qs = (
        Marks.objects.filter(student=student)
        .select_related("exam", "subject")
        .order_by("-exam__date", "-exam__id", "subject__name")
    )
    mark_rows = []
    for m in marks_qs:
        total = m.total_marks or 0
        obtained = m.marks_obtained or 0
        pct = round((obtained / total * 100) if total else 0, 1)
        exam_name = m.exam.name if m.exam else (m.exam_name or "—")
        exam_date = m.exam.date if m.exam else m.exam_date
        if m.exam_id and m.exam and getattr(m.exam, "session_id", None):
            detail_url = reverse("core:student_exam_session_detail", args=[m.exam.session_id])
        elif m.exam_id:
            detail_url = reverse("core:student_exam_detail_by_id", args=[m.exam_id])
        else:
            detail_url = None
        mark_rows.append({
            "exam_id": m.exam_id,
            "exam_name": exam_name,
            "exam_date": exam_date,
            "subject": m.subject.name if m.subject else "—",
            "marks_obtained": obtained,
            "total_marks": total,
            "pct": pct,
            "grade": _grade_from_pct(pct),
            "detail_url": detail_url,
        })
    return render(
        request,
        "core/student_dashboard/marks.html",
        {
            "mark_rows": mark_rows,
            "exam_summaries": _student_exam_summaries(student),
        },
    )


@student_required
def student_exams_list(request):
    if not has_feature_access(getattr(request.user, "school", None), "exams", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    student = getattr(request.user, "student_profile", None)
    if not student:
        return render(request, "core/student/exams_list.html", {"exams": []})
    exams = _student_exam_summaries(student)
    return render(request, "core/student/exams_list.html", {"exams": exams})


@student_required
def student_exam_session_detail(request, session_id):
    """Student: schedule + marks for all papers under one exam session."""
    if not has_feature_access(getattr(request.user, "school", None), "exams", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    student = getattr(request.user, "student_profile", None)
    if not student or not student.classroom or not student.section:
        raise PermissionDenied
    session_obj = get_object_or_404(
        ExamSession.objects.select_related("classroom", "created_by"),
        pk=session_id,
    )
    if (
        student.classroom.name != session_obj.class_name
        or student.section.name != session_obj.section
    ):
        raise PermissionDenied

    papers = list(
        _exam_papers_full_qs()
        .filter(session=session_obj)
        .order_by("date", "subject__name")
    )
    marks_by_exam_id = {
        m.exam_id: m
        for m in Marks.objects.filter(student=student, exam__session=session_obj).select_related(
            "subject", "exam"
        )
    }
    schedule_rows = []
    total_o = total_m = 0
    for p in papers:
        mk = marks_by_exam_id.get(p.id)
        if mk:
            total_o += mk.marks_obtained
            total_m += mk.total_marks
        pct = (
            round((mk.marks_obtained / mk.total_marks * 100), 1)
            if mk and mk.total_marks
            else None
        )
        schedule_rows.append({
            "paper": p,
            "subject": p.subject.name if p.subject else "—",
            "date": p.date,
            "start_time": p.start_time,
            "end_time": p.end_time,
            "mark": mk,
            "pct": pct,
            "grade": _grade_from_pct(pct) if pct is not None else "—",
        })
    overall_pct = round((total_o / total_m * 100), 1) if total_m else None
    return render(
        request,
        "core/student/exam_session_detail.html",
        {
            "session": session_obj,
            "schedule_rows": schedule_rows,
            "overall_pct": overall_pct,
            "grade": _grade_from_pct(overall_pct) if overall_pct is not None else "—",
        },
    )


@student_required
def student_exam_detail_by_id(request, exam_id):
    """Detail for a single exam paper (legacy). Session-based papers redirect to session view."""
    if not has_feature_access(getattr(request.user, "school", None), "exams", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    exam = get_object_or_404(_exam_read_qs(), pk=exam_id)
    if exam.session_id:
        return redirect("core:student_exam_session_detail", session_id=exam.session_id)
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
    if not has_feature_access(getattr(request.user, "school", None), "exams", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
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
    if not has_feature_access(getattr(request.user, "school", None), "reports", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
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


def _teacher_display_name(teacher):
    if not teacher or not getattr(teacher, "user", None):
        return "—"
    return teacher.user.get_full_name() or teacher.user.username or "—"


def _student_report_card_context(student, exam=None, session=None):
    """
    Build report card context for one exam paper or a whole exam session (multi-subject).
    Pass exactly one of exam= or session=.
    """
    if (exam is None) == (session is None):
        raise ValueError("Provide exactly one of exam= or session=")
    if session is not None:
        return _student_report_card_context_for_session(student, session)
    return _student_report_card_context_for_exam(student, exam)


def _student_report_card_context_for_exam(student, exam):
    marks_qs = Marks.objects.filter(student=student, exam=exam).select_related(
        "subject", "exam", "exam__teacher__user"
    )
    marks_list = list(marks_qs)
    if not marks_list:
        raise Http404

    teacher_name = _teacher_display_name(getattr(exam, "teacher", None))
    rows = []
    total_obtained = 0
    total_max = 0
    for m in marks_list:
        pct = round((m.marks_obtained / m.total_marks * 100) if m.total_marks else 0, 1)
        total_obtained += m.marks_obtained
        total_max += m.total_marks
        rows.append(
            {
                "subject": m.subject.name if m.subject else "—",
                "marks_obtained": m.marks_obtained,
                "total_marks": m.total_marks,
                "pct": pct,
                "grade": _grade_from_pct(pct),
                "teacher_name": teacher_name,
                "paper_date": exam.date,
            }
        )
    overall_pct = round((total_obtained / total_max * 100) if total_max else 0, 1)
    overall_grade = _grade_from_pct(overall_pct)

    att_qs = Attendance.objects.filter(student=student)
    total_att_days = att_qs.count()
    present_att_days = att_qs.filter(status=Attendance.Status.PRESENT).count()
    attendance_pct = round((present_att_days / total_att_days * 100) if total_att_days else 0, 1)

    school = student.user.school
    ref_date = exam.date
    academic_year = f"{ref_date.year}-{ref_date.year + 1}" if ref_date else ""

    ai_remarks = _report_card_ai_remarks(school, rows)

    return {
        "school": school,
        "student": student,
        "exam": exam,
        "session": None,
        "is_session": False,
        "report_title": exam.name,
        "exam_date_display": ref_date,
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
        "today": timezone.localdate(),
    }


def _student_report_card_context_for_session(student, session_obj):
    papers = list(
        _exam_papers_full_qs()
        .filter(session=session_obj)
        .order_by("date", "subject__name")
    )
    if not papers:
        raise Http404

    marks_by = {
        m.exam_id: m
        for m in Marks.objects.filter(student=student, exam__session=session_obj).select_related(
            "subject", "exam", "exam__teacher__user"
        )
    }

    rows = []
    total_obtained = 0
    total_max = 0
    dates = [p.date for p in papers if p.date]
    for p in papers:
        mk = marks_by.get(p.id)
        default_total = p.total_marks if getattr(p, "total_marks", None) else 100
        tname = _teacher_display_name(getattr(p, "teacher", None))
        if mk:
            pct = round((mk.marks_obtained / mk.total_marks * 100) if mk.total_marks else 0, 1)
            total_obtained += mk.marks_obtained
            total_max += mk.total_marks
            rows.append(
                {
                    "subject": p.subject.name if p.subject else p.name,
                    "marks_obtained": mk.marks_obtained,
                    "total_marks": mk.total_marks,
                    "pct": pct,
                    "grade": _grade_from_pct(pct),
                    "teacher_name": tname,
                    "paper_date": p.date,
                }
            )
        else:
            rows.append(
                {
                    "subject": p.subject.name if p.subject else p.name,
                    "marks_obtained": None,
                    "total_marks": default_total,
                    "pct": None,
                    "grade": "—",
                    "teacher_name": tname,
                    "paper_date": p.date,
                }
            )

    overall_pct = round((total_obtained / total_max * 100), 1) if total_max else None
    overall_grade = _grade_from_pct(overall_pct) if overall_pct is not None else "—"

    att_qs = Attendance.objects.filter(student=student)
    total_att_days = att_qs.count()
    present_att_days = att_qs.filter(status=Attendance.Status.PRESENT).count()
    attendance_pct = round((present_att_days / total_att_days * 100) if total_att_days else 0, 1)

    school = student.user.school
    dmin, dmax = (min(dates), max(dates)) if dates else (None, None)
    if dmin and dmax:
        academic_year = f"{dmin.year}-{dmin.year + 1}"
        if dmin == dmax:
            exam_date_display = dmin
        else:
            exam_date_display = None
            exam_date_range = (dmin, dmax)
    else:
        academic_year = ""
        exam_date_display = None
        exam_date_range = None

    ai_rows = [r for r in rows if r["pct"] is not None]
    ai_remarks = _report_card_ai_remarks(school, ai_rows)

    ctx = {
        "school": school,
        "student": student,
        "exam": None,
        "session": session_obj,
        "is_session": True,
        "report_title": session_obj.name,
        "exam_date_display": exam_date_display,
        "exam_date_range": exam_date_range,
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
        "today": timezone.localdate(),
    }
    return ctx


def _report_card_ai_remarks(school, rows):
    if not school or not school.has_feature("ai_marksheet_summaries") or not rows:
        return ""
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
    return "Student shows " + " but ".join(parts) + "." if parts else ""


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
    exam = get_object_or_404(_exam_read_qs(), pk=exam_id)
    if not student.classroom or not student.section:
        raise PermissionDenied
    if student.classroom.name != exam.class_name or student.section.name != exam.section:
        raise PermissionDenied
    if exam.session_id:
        return redirect("core:student_report_card_session_view", session_id=exam.session_id)
    context = _student_report_card_context(student, exam=exam)
    return render(request, "core/student/report_card_view.html", context)


@student_required
@feature_required("reports")
def student_report_card_session_view(request, session_id):
    """Report card for all subject papers under one exam session."""
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    session_obj = get_object_or_404(
        ExamSession.objects.select_related("classroom", "created_by"),
        pk=session_id,
    )
    if not student.classroom or not student.section:
        raise PermissionDenied
    if (
        student.classroom.name != session_obj.class_name
        or student.section.name != session_obj.section
    ):
        raise PermissionDenied
    context = _student_report_card_context(student, session=session_obj)
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
    Session-based papers redirect to the session PDF (all subjects on one card).
    """
    from django.utils.text import slugify

    from .pdf_utils import render_pdf_bytes, pdf_response

    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    exam = get_object_or_404(_exam_read_qs(), pk=exam_id)
    if not student.classroom or not student.section:
        raise PermissionDenied
    if student.classroom.name != exam.class_name or student.section.name != exam.section:
        raise PermissionDenied
    if exam.session_id:
        return redirect("core:student_report_card_session_pdf", session_id=exam.session_id)
    context = _student_report_card_context(student, exam=exam)

    pdf = render_pdf_bytes("core/student/report_card_pdf.html", context)
    if pdf is None:
        return redirect("core:student_reports")
    filename = f"report-card-{slugify(exam.name)}.pdf"
    return pdf_response(pdf, filename)


@student_required
@feature_required("reports")
def student_report_card_session_pdf(request, session_id):
    from django.utils.text import slugify

    from .pdf_utils import render_pdf_bytes, pdf_response

    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    session_obj = get_object_or_404(ExamSession.objects.all(), pk=session_id)
    if not student.classroom or not student.section:
        raise PermissionDenied
    if (
        student.classroom.name != session_obj.class_name
        or student.section.name != session_obj.section
    ):
        raise PermissionDenied
    context = _student_report_card_context(student, session=session_obj)
    pdf = render_pdf_bytes("core/student/report_card_pdf.html", context)
    if pdf is None:
        return redirect("core:student_reports")
    filename = f"report-card-{slugify(session_obj.name)}.pdf"
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
    """List students (master details only) with filters and pagination."""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    from django.core.paginator import Paginator
    from django.db.models import Count
    from django.http import HttpResponse
    from django.utils import timezone
    import csv

    from apps.school_data.models import Attendance, AcademicYear

    # Build an optimized queryset based on actual model relations.
    # Some deployments may not have a direct Student->ClassRoom FK named `classroom`;
    # in that case we fall back to Section->ClassRoom (section__classroom) and expose
    # a runtime `student.classroom` attribute for template compatibility.
    select_related_fields = ["user", "section", "academic_year"]
    classroom_source = None  # "student" | "section" | None

    def _student_field(name: str):
        try:
            return Student._meta.get_field(name)
        except Exception:
            return None

    classroom_field = _student_field("classroom")
    if classroom_field and getattr(classroom_field, "is_relation", False) and (
        getattr(classroom_field, "many_to_one", False) or getattr(classroom_field, "one_to_one", False)
    ):
        select_related_fields.append("classroom")
        classroom_source = "student"
    else:
        # Fallback: Section.classroom if it exists and is FK/O2O
        try:
            sec_classroom_field = Section._meta.get_field("classroom")
            if getattr(sec_classroom_field, "many_to_one", False) or getattr(sec_classroom_field, "one_to_one", False):
                select_related_fields.append("section__classroom")
                classroom_source = "section"
        except Exception:
            pass

    base_qs = Student.objects.all().select_related(*select_related_fields)

    # -------------------
    # Top summary cards
    # -------------------
    today = timezone.localdate()
    month_start = today.replace(day=1)

    total_students = base_qs.count()
    active_students = base_qs.filter(user__is_active=True).count()
    withdrawn_students = base_qs.filter(user__is_active=False).count()

    present_today = Attendance.objects.filter(date=today, status=Attendance.Status.PRESENT).values("student_id").distinct().count()
    absent_today = Attendance.objects.filter(date=today, status=Attendance.Status.ABSENT).values("student_id").distinct().count()

    new_admissions_month = base_qs.filter(user__date_joined__date__gte=month_start).count()

    gender_counts = (
        base_qs.values("gender")
        .annotate(c=Count("id"))
    )
    gender_map = {row["gender"] or "": row["c"] for row in gender_counts}

    stats = {
        "total": total_students,
        "active": active_students,
        "present_today": present_today,
        "absent_today": absent_today,
        "new_admissions_month": new_admissions_month,
        "withdrawn": withdrawn_students,
        "boys": gender_map.get("M", 0),
        "girls": gender_map.get("F", 0),
        "other_gender": gender_map.get("O", 0),
    }

    # -------------------
    # Filters (GET)
    # -------------------
    q = (request.GET.get("q") or "").strip()
    admission = (request.GET.get("admission") or "").strip()
    roll = (request.GET.get("roll") or "").strip()
    classroom_id = (request.GET.get("classroom") or "").strip()
    section_id = (request.GET.get("section") or "").strip()
    academic_year_id = (request.GET.get("year") or "").strip()
    gender = (request.GET.get("gender") or "").strip()
    status = (request.GET.get("status") or "").strip().lower()  # active/inactive
    branch = (request.GET.get("branch") or "").strip()
    per_page_raw = (request.GET.get("per_page") or "").strip()

    qs = base_qs
    if q:
        qs = qs.filter(
            Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(user__username__icontains=q)
        )
    if admission:
        qs = qs.filter(admission_number__icontains=admission)
    if roll:
        qs = qs.filter(roll_number__icontains=roll)
    if classroom_id.isdigit():
        cid = int(classroom_id)
        if classroom_source == "student":
            qs = qs.filter(classroom_id=cid)
        elif classroom_source == "section":
            qs = qs.filter(section__classroom_id=cid)
        else:
            # No known classroom relation; ignore this filter safely.
            pass
    if section_id.isdigit():
        qs = qs.filter(section_id=int(section_id))
    if academic_year_id.isdigit():
        qs = qs.filter(academic_year_id=int(academic_year_id))
    if gender in {"M", "F", "O"}:
        qs = qs.filter(gender=gender)
    if status == "active":
        qs = qs.filter(user__is_active=True)
    elif status == "inactive":
        qs = qs.filter(user__is_active=False)
    qs = qs.distinct()

    # Export CSV (filtered set)
    export = (request.GET.get("export") or "").strip().lower()
    # Ordering
    if classroom_source == "student":
        order_by = ("classroom__name", "section__name", "roll_number")
    elif classroom_source == "section":
        order_by = ("section__classroom__name", "section__name", "roll_number")
    else:
        order_by = ("section__name", "roll_number")

    if export == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = "attachment; filename=students.csv"
        w = csv.writer(resp)
        w.writerow(["Admission No", "Roll No", "Student Name", "Gender", "Class", "Section", "Parent Name", "Parent Phone", "Admission Date", "Status"])
        for s in qs.order_by(*order_by):
            if classroom_source == "section" and getattr(s, "classroom", None) is None and getattr(s, "section", None):
                try:
                    s.classroom = s.section.classroom
                except Exception:
                    pass
            extra = getattr(s, "extra_data", {}) or {}
            adm_date = (
                (extra.get("academic", {}) or {}).get("admission_date")
                or ""
            )
            w.writerow([
                s.admission_number or "",
                s.roll_number or "",
                (s.user.get_full_name() or s.user.username),
                (s.get_gender_display() if getattr(s, "gender", "") else ""),
                str(getattr(s, "classroom", None) or ""),
                (s.section.name if s.section else ""),
                s.parent_name or "",
                s.parent_phone or "",
                adm_date,
                ("Active" if s.user.is_active else "Inactive"),
            ])
        return resp

    # Pagination: default 20, allow user to choose common sizes.
    per_page = 20
    if per_page_raw.isdigit():
        per_page = int(per_page_raw)
    if per_page not in {10, 20, 50, 100}:
        per_page = 20

    paginator = Paginator(qs.order_by(*order_by), per_page)
    page = request.GET.get("page", 1)
    students = paginator.get_page(page)

    for s in students.object_list:
        if classroom_source == "section" and getattr(s, "classroom", None) is None and getattr(s, "section", None):
            try:
                s.classroom = s.section.classroom
            except Exception:
                pass
        extra = getattr(s, "extra_data", {}) or {}
        s.admission_date_display = (
            (extra.get("academic", {}) or {}).get("admission_date")
            or (s.user.date_joined.date().isoformat() if getattr(s.user, "date_joined", None) else "")
        )

    classrooms = ClassRoom.objects.select_related("academic_year").order_by("academic_year__start_date", "name")
    sections = Section.objects.all().order_by("name")
    years = AcademicYear.objects.order_by("-start_date")

    return render(request, "core/school/students_list.html", {
        "students": students,
        "classrooms": classrooms,
        "sections": sections,
        "years": years,
        "stats": stats,
        "school": school,
        "filters": {
            "q": q,
            "admission": admission,
            "roll": roll,
            "classroom_id": classroom_id,
            "section_id": section_id,
            "year": academic_year_id,
            "gender": gender,
            "status": status,
            "branch": branch,
            "per_page": str(per_page),
        },
    })


@admin_required
def school_student_add(request):
    """Add new student. Username = Admission Number (auto-generated if empty)."""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    from .forms import StudentAddForm
    from apps.school_data.models import StudentDocument

    def _generate_admission_number() -> str:
        """
        Generate a tenant-unique admission number like ADM2026-0001.
        Keeps it predictable and sortable; avoids schema-level sequences.
        """
        year = timezone.localdate().year
        prefix = f"ADM{year}-"
        # Find max existing for this prefix and increment.
        last = (
            Student.objects.filter(admission_number__startswith=prefix)
            .order_by("-admission_number")
            .values_list("admission_number", flat=True)
            .first()
        )
        next_n = 1
        if last and isinstance(last, str) and last.startswith(prefix):
            try:
                next_n = int(last.replace(prefix, "").strip() or "0") + 1
            except Exception:
                next_n = 1
        return f"{prefix}{next_n:04d}"

    # IMPORTANT: for forms.Form, pass files via `files=` kwarg.
    form = StudentAddForm(school, data=(request.POST or None), files=(request.FILES or None))
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        first_name = data["first_name"]
        middle_name = (data.get("middle_name") or "").strip()
        last_name = (data.get("last_name") or "").strip()
        roll_number = data["roll_number"]
        admission_number = (data.get("admission_number") or "").strip().upper() or _generate_admission_number()
        password = data.get("password") or f"{first_name.capitalize()}@123"
        record_status = (data.get("record_status") or "ACTIVE").strip().upper()
        is_active = record_status == "ACTIVE"

        with transaction.atomic():
            user = User.objects.create_user(
                username=admission_number,
                password=password,
                first_name=first_name,
                last_name=(" ".join([middle_name, last_name]).strip()),
                email=(data.get("email") or "").strip(),
                role=User.Roles.STUDENT,
                school=school,
                is_first_login=True,
                is_active=is_active,
            )
            student = Student(
                user=user,
                classroom=data.get("classroom"),
                section=data.get("section"),
                roll_number=roll_number,
                admission_number=admission_number,
                date_of_birth=data.get("date_of_birth"),
                gender=data.get("gender") or "",
                parent_name=data.get("parent_name") or "",
                parent_phone=data.get("parent_phone") or "",
                academic_year=data.get("academic_year"),
                phone=(data.get("student_mobile") or "").strip() or None,
                address=("\n".join([data.get("address_line1") or "", data.get("address_line2") or ""]).strip() or None),
                profile_image=data.get("profile_image"),
            )
            # Store extended admission details in flexible JSON.
            student.extra_data = {
                "basic": {
                    "middle_name": middle_name,
                    "blood_group": data.get("blood_group") or "",
                    "id_number": data.get("id_number") or "",
                    "nationality": data.get("nationality") or "",
                    "religion": data.get("religion") or "",
                    "mother_tongue": data.get("mother_tongue") or "",
                },
                "academic": {
                    "admission_date": str(data.get("admission_date") or ""),
                    "registration_number": data.get("registration_number") or "",
                    "course_branch": data.get("course_branch") or "",
                    "semester_year": data.get("semester_year") or "",
                    "stream": data.get("stream") or "",
                    "student_type": data.get("student_type") or "",
                    "previous_institution": data.get("previous_institution") or "",
                    "previous_marks": data.get("previous_marks") or "",
                },
                "parents": {
                    "father_name": data.get("father_name") or "",
                    "father_mobile": data.get("father_mobile") or "",
                    "father_occupation": data.get("father_occupation") or "",
                    "mother_name": data.get("mother_name") or "",
                    "mother_mobile": data.get("mother_mobile") or "",
                    "mother_occupation": data.get("mother_occupation") or "",
                    "guardian_name": data.get("guardian_name") or "",
                    "guardian_relation": data.get("guardian_relation") or "",
                    "guardian_phone": data.get("guardian_phone") or "",
                    "student_email": (data.get("student_email") or "").strip(),
                },
                "contact": {
                    "city": data.get("city") or "",
                    "district": data.get("district") or "",
                    "state": data.get("state") or "",
                    "pincode": data.get("pincode") or "",
                    "country": data.get("country") or "",
                },
                "medical": {
                    "emergency_contact_name": data.get("emergency_contact_name") or "",
                    "emergency_phone": data.get("emergency_phone") or "",
                    "allergies": data.get("allergies") or "",
                    "medical_conditions": data.get("medical_conditions") or "",
                    "doctor_name": data.get("doctor_name") or "",
                    "hospital": data.get("hospital") or "",
                    "insurance_details": data.get("insurance_details") or "",
                },
                "transport_hostel": {
                    "transport_required": data.get("transport_required") or "NO",
                    "route_id": (data.get("route").id if data.get("route") else None),
                    "pickup_point": data.get("pickup_point") or "",
                    "hostel_required": data.get("hostel_required") or "NO",
                    "hostel_room_id": (data.get("hostel_room").id if data.get("hostel_room") else None),
                },
                "billing": {
                    "scholarship": data.get("scholarship") or "",
                    "discount_percent": str(data.get("discount_percent") or ""),
                    "installment_type": data.get("installment_type") or "",
                    "first_payment_amount": str(data.get("first_payment_amount") or ""),
                    "payment_due_date": str(data.get("payment_due_date") or ""),
                },
                "status": {
                    "record_status": record_status,
                },
            }
            student.save_with_audit(request.user)

            # Store uploaded documents (optional).
            doc_map = [
                ("doc_birth_certificate", StudentDocument.DocType.BIRTH_CERT, "Birth Certificate"),
                ("doc_transfer_certificate", StudentDocument.DocType.TRANSFER_CERT, "Transfer Certificate"),
                ("doc_id_proof", StudentDocument.DocType.OTHER, "Aadhar / ID Proof"),
                ("doc_previous_marks", StudentDocument.DocType.OTHER, "Previous Marks Memo"),
                ("doc_passport_photo", StudentDocument.DocType.PHOTO, "Passport Photo"),
                ("doc_parent_id", StudentDocument.DocType.OTHER, "Parent ID"),
            ]
            for field_name, doc_type, title in doc_map:
                f = request.FILES.get(field_name)
                if not f:
                    continue
                StudentDocument.objects.create(
                    student=student,
                    doc_type=doc_type,
                    title=title,
                    file=f,
                    uploaded_by=request.user,
                )

        # Send credentials email (best-effort)
        email_sent = False
        if data.get("email"):
            try:
                from django.core.mail import send_mail
                from django.conf import settings

                from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@schoolerp.local")
                subject = "Your Campus ERP Login Credentials"
                body = (
                    f"Hello {first_name},\n\n"
                    f"Your student account has been created.\n\n"
                    f"Username: Your Admission Number ({admission_number})\n"
                    f"Password: {password}\n\n"
                    "Please change your password after first login.\n\n"
                    "— Campus Admin"
                )
                send_mail(subject, body, from_email, [data["email"]], fail_silently=True)
                email_sent = True
            except Exception:
                pass

        msg = "Student created successfully."
        if email_sent:
            msg += " Login credentials sent to email."
        else:
            msg += f" Username (Admission Number): {admission_number} | Password: {password}"
        messages.success(request, msg)
        return redirect("core:school_students_list")
    return render(request, "core/school/student_add.html", {"form": form})


def _student_record_letterhead_context(request, school, student):
    """
    Letterhead data for student record (HTML print view + PDF).
    School model carries address (multiline), phone, email, custom_domain, header_text (board/affiliation), logo.
    """
    lines = [ln.strip() for ln in (school.address or "").splitlines() if ln.strip()]
    street = lines[0] if lines else ""
    rest = lines[1:] if len(lines) > 1 else []
    dom = (school.custom_domain or "").strip()
    website = None
    if dom:
        website = dom if dom.lower().startswith(("http://", "https://")) else f"https://{dom}"
    logo_url = None
    if school.logo:
        try:
            logo_url = request.build_absolute_uri(school.logo.url)
        except Exception:
            logo_url = school.logo.url or None
    affiliation = (school.header_text or "").strip() or None
    return {
        "school": school,
        "letterhead_address_street": street or None,
        "letterhead_address_rest": rest,
        "letterhead_website": website,
        "letterhead_logo_url": logo_url,
        "letterhead_affiliation": affiliation,
    }


@admin_required
def school_student_view(request, student_id):
    """View student details (read-only) — all profile fields including extra_data."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    student = get_object_or_404(
        Student.objects.select_related(
            "user",
            "classroom",
            "section",
            "academic_year",
            "created_by",
            "modified_by",
        ),
        id=student_id,
    )
    from apps.school_data.models import HostelRoom, Route, StudentDocument

    extra = student.extra_data or {}
    basic = extra.get("basic") or {}
    academic = extra.get("academic") or {}
    parents = extra.get("parents") or {}
    contact = extra.get("contact") or {}
    medical = extra.get("medical") or {}
    th = extra.get("transport_hostel") or {}
    billing = extra.get("billing") or {}
    status_block = extra.get("status") or {}

    route = Route.objects.filter(id=th.get("route_id")).first() if th.get("route_id") else None
    hostel_room = HostelRoom.objects.filter(id=th.get("hostel_room_id")).first() if th.get("hostel_room_id") else None

    addr_lines = (student.address or "").split("\n") if student.address else []
    address_line1 = addr_lines[0] if len(addr_lines) > 0 else ""
    address_line2 = addr_lines[1] if len(addr_lines) > 1 else ""

    att_qs = Attendance.objects.filter(student=student)
    if student.academic_year_id:
        att_qs = att_qs.filter(
            Q(academic_year_id=student.academic_year_id) | Q(academic_year__isnull=True)
        )
    att_total = att_qs.count()
    att_present = att_qs.filter(status=Attendance.Status.PRESENT).count()
    stats_attendance = {
        "total": att_total,
        "present": att_present,
        "pct": round((att_present / att_total * 100), 1) if att_total else None,
    }
    mark_pct_vals = []
    for m in Marks.objects.filter(student=student).only("marks_obtained", "total_marks"):
        if m.total_marks:
            mark_pct_vals.append((m.marks_obtained / m.total_marks) * 100)
    stats_exams = {
        "pct": round(sum(mark_pct_vals) / len(mark_pct_vals), 1) if mark_pct_vals else None,
        "count": len(mark_pct_vals),
    }

    ctx = {
        "student": student,
        "basic": basic,
        "academic": academic,
        "parents": parents,
        "contact": contact,
        "medical": medical,
        "th": th,
        "billing": billing,
        "status_block": status_block,
        "address_line1": address_line1,
        "address_line2": address_line2,
        "route_obj": route,
        "hostel_room_obj": hostel_room,
        "documents": StudentDocument.objects.filter(student=student).select_related("uploaded_by"),
        "stats_attendance": stats_attendance,
        "stats_exams": stats_exams,
    }
    return render(request, "core/school/student_view.html", ctx)


@admin_required
def school_student_edit(request, student_id):
    """Edit student. Only students of logged-in user's school."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    student = get_object_or_404(Student, id=student_id)
    from .forms import StudentMasterEditForm
    from apps.school_data.models import StudentDocument

    extra = student.extra_data or {}
    basic = extra.get("basic", {}) or {}
    academic = extra.get("academic", {}) or {}
    parents = extra.get("parents", {}) or {}
    contact = extra.get("contact", {}) or {}
    medical = extra.get("medical", {}) or {}
    th = extra.get("transport_hostel", {}) or {}
    billing = extra.get("billing", {}) or {}
    status_block = extra.get("status", {}) or {}

    initial = {
        # Basic
        "first_name": student.user.first_name,
        "middle_name": basic.get("middle_name") or "",
        "last_name": student.user.last_name,
        "date_of_birth": student.date_of_birth,
        "gender": student.gender or "",
        "blood_group": basic.get("blood_group") or "",
        "id_number": basic.get("id_number") or "",
        "nationality": basic.get("nationality") or "",
        "religion": basic.get("religion") or "",
        "mother_tongue": basic.get("mother_tongue") or "",

        # Academic
        "academic_year": student.academic_year,
        "admission_date": academic.get("admission_date") or "",
        "classroom": student.classroom,
        "section": student.section,
        "admission_number": student.admission_number or "",
        "roll_number": student.roll_number,
        "registration_number": academic.get("registration_number") or "",
        "course_branch": academic.get("course_branch") or "",
        "semester_year": academic.get("semester_year") or "",
        "previous_institution": academic.get("previous_institution") or "",
        "previous_marks": academic.get("previous_marks") or "",
        "stream": academic.get("stream") or "",
        "student_type": academic.get("student_type") or "",

        # Parents
        "father_name": parents.get("father_name") or "",
        "father_mobile": parents.get("father_mobile") or "",
        "father_occupation": parents.get("father_occupation") or "",
        "mother_name": parents.get("mother_name") or "",
        "mother_mobile": parents.get("mother_mobile") or "",
        "mother_occupation": parents.get("mother_occupation") or "",
        "guardian_name": parents.get("guardian_name") or "",
        "guardian_relation": parents.get("guardian_relation") or "",
        "guardian_phone": parents.get("guardian_phone") or "",
        "parent_name": student.parent_name or "",
        "parent_phone": student.parent_phone or "",
        "email": student.user.email or "",

        # Contact
        "student_mobile": student.phone or "",
        "student_email": parents.get("student_email") or "",
        "address_line1": (student.address or "").split("\n")[0] if student.address else "",
        "address_line2": (student.address or "").split("\n")[1] if student.address and "\n" in student.address else "",
        "city": contact.get("city") or "",
        "district": contact.get("district") or "",
        "state": contact.get("state") or "",
        "pincode": contact.get("pincode") or "",
        "country": contact.get("country") or "",

        # Medical
        "emergency_contact_name": medical.get("emergency_contact_name") or "",
        "emergency_phone": medical.get("emergency_phone") or "",
        "allergies": medical.get("allergies") or "",
        "medical_conditions": medical.get("medical_conditions") or "",
        "doctor_name": medical.get("doctor_name") or "",
        "hospital": medical.get("hospital") or "",
        "insurance_details": medical.get("insurance_details") or "",

        # Transport / hostel
        "transport_required": th.get("transport_required") or "NO",
        "route": (Route.objects.filter(id=th.get("route_id")).first() if th.get("route_id") else None),
        "pickup_point": th.get("pickup_point") or "",
        "hostel_required": th.get("hostel_required") or "NO",
        "hostel_room": (HostelRoom.objects.filter(id=th.get("hostel_room_id")).first() if th.get("hostel_room_id") else None),

        # Billing prefs (kept optional)
        "scholarship": billing.get("scholarship") or "",
        "discount_percent": billing.get("discount_percent") or "",
        "installment_type": billing.get("installment_type") or "",
        "first_payment_amount": billing.get("first_payment_amount") or "",
        "payment_due_date": billing.get("payment_due_date") or "",

        # Status
        "record_status": status_block.get("record_status") or ("ACTIVE" if student.user.is_active else "INACTIVE"),
    }

    form = StudentMasterEditForm(
        school,
        student=student,
        data=(request.POST or None),
        files=(request.FILES or None),
        initial=initial,
    )

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        record_status = (data.get("record_status") or "ACTIVE").strip().upper()
        is_active = record_status == "ACTIVE"

        new_adm = (data.get("admission_number") or "").strip().upper()
        with transaction.atomic():
            # User
            student.user.first_name = data.get("first_name") or ""
            student.user.last_name = " ".join([(data.get("middle_name") or "").strip(), (data.get("last_name") or "").strip()]).strip()
            student.user.email = (data.get("email") or "").strip()
            student.user.is_active = is_active
            if new_adm and new_adm != (student.user.username or ""):
                student.user.username = new_adm
            student.user.save()

            # Student core
            student.classroom = data.get("classroom")
            student.section = data.get("section")
            student.academic_year = data.get("academic_year")
            student.roll_number = data.get("roll_number")
            if new_adm:
                student.admission_number = new_adm
            student.date_of_birth = data.get("date_of_birth")
            student.gender = data.get("gender") or ""
            student.parent_name = data.get("parent_name") or ""
            student.parent_phone = data.get("parent_phone") or ""
            student.phone = (data.get("student_mobile") or "").strip() or None
            student.address = ("\n".join([data.get("address_line1") or "", data.get("address_line2") or ""]).strip() or None)
            if data.get("profile_image"):
                student.profile_image = data.get("profile_image")

            student.extra_data = {
                "basic": {
                    "middle_name": (data.get("middle_name") or "").strip(),
                    "blood_group": data.get("blood_group") or "",
                    "id_number": data.get("id_number") or "",
                    "nationality": data.get("nationality") or "",
                    "religion": data.get("religion") or "",
                    "mother_tongue": data.get("mother_tongue") or "",
                },
                "academic": {
                    "admission_date": str(data.get("admission_date") or ""),
                    "registration_number": data.get("registration_number") or "",
                    "course_branch": data.get("course_branch") or "",
                    "semester_year": data.get("semester_year") or "",
                    "stream": data.get("stream") or "",
                    "student_type": data.get("student_type") or "",
                    "previous_institution": data.get("previous_institution") or "",
                    "previous_marks": data.get("previous_marks") or "",
                },
                "parents": {
                    "father_name": data.get("father_name") or "",
                    "father_mobile": data.get("father_mobile") or "",
                    "father_occupation": data.get("father_occupation") or "",
                    "mother_name": data.get("mother_name") or "",
                    "mother_mobile": data.get("mother_mobile") or "",
                    "mother_occupation": data.get("mother_occupation") or "",
                    "guardian_name": data.get("guardian_name") or "",
                    "guardian_relation": data.get("guardian_relation") or "",
                    "guardian_phone": data.get("guardian_phone") or "",
                    "student_email": (data.get("student_email") or "").strip(),
                },
                "contact": {
                    "city": data.get("city") or "",
                    "district": data.get("district") or "",
                    "state": data.get("state") or "",
                    "pincode": data.get("pincode") or "",
                    "country": data.get("country") or "",
                },
                "medical": {
                    "emergency_contact_name": data.get("emergency_contact_name") or "",
                    "emergency_phone": data.get("emergency_phone") or "",
                    "allergies": data.get("allergies") or "",
                    "medical_conditions": data.get("medical_conditions") or "",
                    "doctor_name": data.get("doctor_name") or "",
                    "hospital": data.get("hospital") or "",
                    "insurance_details": data.get("insurance_details") or "",
                },
                "transport_hostel": {
                    "transport_required": data.get("transport_required") or "NO",
                    "route_id": (data.get("route").id if data.get("route") else None),
                    "pickup_point": data.get("pickup_point") or "",
                    "hostel_required": data.get("hostel_required") or "NO",
                    "hostel_room_id": (data.get("hostel_room").id if data.get("hostel_room") else None),
                },
                "billing": {
                    "scholarship": data.get("scholarship") or "",
                    "discount_percent": str(data.get("discount_percent") or ""),
                    "installment_type": data.get("installment_type") or "",
                    "first_payment_amount": str(data.get("first_payment_amount") or ""),
                    "payment_due_date": str(data.get("payment_due_date") or ""),
                },
                "status": {
                    "record_status": record_status,
                },
            }
            student.save_with_audit(request.user)

            # Optional documents uploaded during edit
            doc_map = [
                ("doc_birth_certificate", StudentDocument.DocType.BIRTH_CERT, "Birth Certificate"),
                ("doc_transfer_certificate", StudentDocument.DocType.TRANSFER_CERT, "Transfer Certificate"),
                ("doc_id_proof", StudentDocument.DocType.OTHER, "Aadhar / ID Proof"),
                ("doc_previous_marks", StudentDocument.DocType.OTHER, "Previous Marks Memo"),
                ("doc_passport_photo", StudentDocument.DocType.PHOTO, "Passport Photo"),
                ("doc_parent_id", StudentDocument.DocType.OTHER, "Parent ID"),
            ]
            for field_name, doc_type, title in doc_map:
                f = request.FILES.get(field_name)
                if not f:
                    continue
                StudentDocument.objects.create(
                    student=student,
                    doc_type=doc_type,
                    title=title,
                    file=f,
                    uploaded_by=request.user,
                )

        messages.success(request, "Student updated successfully.")
        return redirect("core:school_student_view", student_id=student.id)

    return render(request, "core/school/student_edit.html", {"form": form, "student": student})


@admin_required
@feature_required("students")
@require_POST
def school_student_delete(request, student_id):
    """Delete student: remove profile first, then linked user (no orphan accounts)."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    student = get_object_or_404(Student, id=student_id)
    if student.user.school_id != school.code:
        messages.error(request, "You cannot delete this student.")
        return redirect("core:school_students_list")
    user = student.user
    display = user.get_full_name() or user.username
    try:
        with transaction.atomic():
            student.delete()
            user.delete()
    except Exception:
        messages.error(
            request,
            "Could not delete student. They may still be linked to fees, attendance, or other records.",
        )
        return redirect("core:school_students_list")
    messages.success(request, f"Student “{display}” deleted successfully.")
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
    from django.utils import timezone
    from django.db.models import Q
    from apps.school_data.models import Subject, ClassRoom

    base_qs = (
        Teacher.objects.all()
        .select_related("user", "user__school")
        .prefetch_related("subjects", "classrooms")
    )

    # -------- Filters (GET) --------
    q = (request.GET.get("q") or "").strip()
    employee_id = (request.GET.get("employee_id") or "").strip()
    subject_id = (request.GET.get("subject") or "").strip()
    class_id = (request.GET.get("classroom") or "").strip()
    status = (request.GET.get("status") or "").strip().lower()  # active/inactive

    qs = base_qs
    if q:
        qs = qs.filter(
            Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(user__username__icontains=q)
        )
    if employee_id:
        qs = qs.filter(employee_id__icontains=employee_id)
    if subject_id.isdigit():
        qs = qs.filter(subjects__id=int(subject_id))
    if class_id.isdigit():
        qs = qs.filter(classrooms__id=int(class_id))
    if status == "active":
        qs = qs.filter(user__is_active=True)
    elif status == "inactive":
        qs = qs.filter(user__is_active=False)

    teachers = qs.distinct()

    # -------- Top Summary Stats (School-wide, not filtered) --------
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    stats = {
        "total": base_qs.count(),
        "active": base_qs.filter(user__is_active=True).count(),
        "inactive": base_qs.filter(user__is_active=False).count(),
        "class_teachers": base_qs.filter(classrooms__isnull=False).distinct().count(),
        "subject_teachers": base_qs.filter(subjects__isnull=False).distinct().count(),
        "new_joiners_month": base_qs.filter(user__date_joined__gte=month_start).count(),
        # "on_leave_today": None  # Only add when leave/attendance model is wired.
    }

    filter_options = {
        "subjects": Subject.objects.order_by("name"),
        "classrooms": ClassRoom.objects.select_related("academic_year").order_by("academic_year__start_date", "name"),
    }

    filters = {
        "q": q,
        "employee_id": employee_id,
        "subject": subject_id,
        "classroom": class_id,
        "status": status,
    }

    return render(
        request,
        "core/school/teachers_list.html",
        {
            "teachers": teachers,
            "stats": stats,
            "filters": filters,
            "filter_options": filter_options,
        },
    )


@admin_required
def school_teacher_add(request):
    """Add new teacher (extended profile, same structure as student master)."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .teacher_forms import TeacherMasterForm

    form = TeacherMasterForm(
        school,
        teacher=None,
        data=request.POST or None,
        files=request.FILES or None,
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        record_status = (data.get("record_status") or "ACTIVE").strip().upper()
        is_active = record_status == "ACTIVE"
        with transaction.atomic():
            user = User.objects.create_user(
                username=data["username"],
                password=data["password"],
                first_name=data.get("first_name") or "",
                last_name=data.get("last_name") or "",
                role=User.Roles.TEACHER,
                school=school,
            )
            user.email = (data.get("email") or "").strip()
            user.is_active = is_active
            user.save()
            addr = "\n".join([data.get("address_line1") or "", data.get("address_line2") or ""]).strip() or None
            teacher = Teacher(
                user=user,
                employee_id=data.get("employee_id") or "",
                phone_number=data.get("phone_number") or "",
                qualification=data.get("qualification") or "",
                experience=data.get("experience") or "",
                date_of_birth=data.get("date_of_birth"),
                gender=data.get("gender") or "",
                address=addr,
                extra_data=TeacherMasterForm.build_extra_data(data),
            )
            if data.get("profile_image"):
                teacher.profile_image = data["profile_image"]
            teacher.save_with_audit(request.user)
            teacher.subjects.set(data.get("subjects") or [])
            teacher.classrooms.set(data.get("classrooms") or [])
        messages.success(request, "Teacher created.")
        return redirect("core:school_teacher_view", teacher_id=teacher.id)
    return render(request, "core/school/teacher_master_form.html", {"form": form, "teacher": None})


@admin_required
def school_teacher_view(request, teacher_id):
    """View teacher profile (read-only, card layout)."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    teacher = get_object_or_404(
        Teacher.objects.select_related("user", "created_by", "modified_by").prefetch_related("subjects", "classrooms"),
        id=teacher_id,
    )
    extra = teacher.extra_data or {}
    basic = extra.get("basic") or {}
    contact = extra.get("contact") or {}
    professional = extra.get("professional") or {}
    family = extra.get("family") or {}
    medical = extra.get("medical") or {}
    payroll = extra.get("payroll") or {}
    status_block = extra.get("status") or {}
    addr_lines = (teacher.address or "").split("\n") if teacher.address else []
    address_line1 = addr_lines[0] if addr_lines else ""
    address_line2 = addr_lines[1] if len(addr_lines) > 1 else ""
    return render(
        request,
        "core/school/teacher_view.html",
        {
            "teacher": teacher,
            "basic": basic,
            "contact": contact,
            "professional": professional,
            "family": family,
            "medical": medical,
            "payroll": payroll,
            "status_block": status_block,
            "address_line1": address_line1,
            "address_line2": address_line2,
        },
    )


@admin_required
def school_teacher_edit(request, teacher_id):
    """Edit teacher extended profile."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    teacher = get_object_or_404(Teacher, id=teacher_id)
    from .teacher_forms import TeacherMasterForm

    form = TeacherMasterForm(
        school,
        teacher=teacher,
        data=request.POST or None,
        files=request.FILES or None,
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        record_status = (data.get("record_status") or "ACTIVE").strip().upper()
        is_active = record_status == "ACTIVE"
        with transaction.atomic():
            teacher.user.first_name = data.get("first_name") or ""
            teacher.user.last_name = data.get("last_name") or ""
            teacher.user.email = (data.get("email") or "").strip()
            teacher.user.role = data.get("role")
            teacher.user.is_active = is_active
            pwd = (data.get("password") or "").strip()
            if pwd:
                teacher.user.set_password(pwd)
            teacher.user.save()

            teacher.employee_id = data.get("employee_id") or ""
            teacher.phone_number = data.get("phone_number") or ""
            teacher.qualification = data.get("qualification") or ""
            teacher.experience = data.get("experience") or ""
            teacher.date_of_birth = data.get("date_of_birth")
            teacher.gender = data.get("gender") or ""
            teacher.address = (
                "\n".join([data.get("address_line1") or "", data.get("address_line2") or ""]).strip() or None
            )
            teacher.extra_data = TeacherMasterForm.build_extra_data(data)
            if data.get("profile_image"):
                teacher.profile_image = data["profile_image"]
            teacher.save_with_audit(request.user)
            teacher.subjects.set(data.get("subjects") or [])
            teacher.classrooms.set(data.get("classrooms") or [])
        messages.success(request, "Teacher updated.")
        return redirect("core:school_teacher_view", teacher_id=teacher.id)
    return render(request, "core/school/teacher_master_form.html", {"form": form, "teacher": teacher})


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
    from django.core.paginator import Paginator

    # PostgreSQL: clear aborted transaction state before running list queries.
    try:
        if getattr(connection, "needs_rollback", False):
            connection.rollback()
    except Exception:
        pass

    # Resolve active year + optional promotion stats first; rollback if promotion
    # schema is missing so paginator/template queries are not run in a bad txn.
    active_year = AcademicYear.objects.filter(is_active=True).order_by("-start_date").first()
    promo_counts = {}
    if active_year:
        try:
            for action, _label in StudentPromotion.Action.choices:
                promo_counts[action] = StudentPromotion.objects.filter(
                    from_year=active_year, action=action
                ).count()
        except (ProgrammingError, InternalError, OperationalError, DatabaseError):
            promo_counts = {}
            try:
                connection.rollback()
            except Exception:
                pass

    qs = AcademicYear.objects.all().order_by("-start_date")
    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(name__icontains=search)
    paginator = Paginator(qs, 15)
    page = request.GET.get("page", 1)
    academic_years = paginator.get_page(page)
    next_year_candidates = (
        AcademicYear.objects.exclude(id=active_year.id).order_by("start_date")
        if active_year
        else AcademicYear.objects.order_by("start_date")
    )
    return render(request, "core/school/academic_year/list.html", {
        "academic_years": academic_years,
        "filters": {"q": search},
        "active_year": active_year,
        "next_year_candidates": next_year_candidates,
        "promo_counts": promo_counts,
    })


@admin_required
def school_academic_year_add(request):
    """Dedicated create page (replaces modal POST on list, which was easy to break)."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import AcademicYearForm

    if request.method == "POST":
        form = AcademicYearForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.save_with_audit(request.user)
            messages.success(request, f'Academic year "{obj.name}" was created successfully.')
            return redirect("core:school_academic_years")
    else:
        form = AcademicYearForm()
    return render(
        request,
        "core/school/academic_year/form.html",
        {
            "form": form,
            "title": "Add Academic Year",
            "academic_year": None,
            "is_add": True,
        },
    )


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
        messages.success(request, f'Academic year "{obj.name}" was saved.')
        return redirect("core:school_academic_years")
    return render(
        request,
        "core/school/academic_year/form.html",
        {
            "form": form,
            "academic_year": ay,
            "title": "Edit Academic Year",
            "is_add": False,
        },
    )


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
    # Keep history safe: block delete if year is referenced.
    try:
        has_related = (
            Student.objects.filter(academic_year=ay).exists()
            or Exam.objects.filter(academic_year=ay).exists()
            or Attendance.objects.filter(academic_year=ay).exists()
            or Fee.objects.filter(academic_year=ay).exists()
            or StudentEnrollment.objects.filter(academic_year=ay).exists()
            or StudentPromotion.objects.filter(Q(from_year=ay) | Q(to_year=ay)).exists()
        )
    except ProgrammingError:
        # If new tables/columns are not migrated yet, fall back to conservative checks.
        has_related = False
        try:
            connection.rollback()
        except Exception:
            pass
        try:
            has_related = Student.objects.filter(classroom__academic_year=ay).exists()
        except ProgrammingError:
            try:
                connection.rollback()
            except Exception:
                pass
        try:
            has_related = has_related or Exam.objects.filter(classroom__academic_year=ay).exists()
        except ProgrammingError:
            try:
                connection.rollback()
            except Exception:
                pass
        try:
            has_related = has_related or Fee.objects.filter(fee_structure__academic_year=ay).exists()
        except ProgrammingError:
            try:
                connection.rollback()
            except Exception:
                pass

    if has_related:
        messages.error(request, "Cannot delete academic year because related student/exam/attendance/fee/promotion data exists.")
        return redirect("core:school_academic_years")
    ay.delete()
    return redirect("core:school_academic_years")


def _extract_grade_num(class_name: str):
    import re

    m = re.search(r"(\d+)", class_name or "")
    return int(m.group(1)) if m else None


def _suggest_promoted_class(current_classroom, target_year):
    if not current_classroom:
        return None
    cur_no = _extract_grade_num(current_classroom.name)
    if cur_no is None:
        return None
    target_no = cur_no + 1
    for c in ClassRoom.objects.filter(academic_year=target_year).order_by("name"):
        if _extract_grade_num(c.name) == target_no:
            return c
    return None


@admin_required
@feature_required("students")
def school_promote_students(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import PromoteStudentsFilterForm, PromoteStudentsActionForm

    filter_form = PromoteStudentsFilterForm(school, request.GET or None)
    action_form = PromoteStudentsActionForm(school, request.POST or None)
    students = []
    preview_rows = []
    from_year = None
    to_year = None

    if filter_form.is_valid():
        from_year = filter_form.cleaned_data["from_year"]
        to_year = filter_form.cleaned_data["to_year"]
        classroom = filter_form.cleaned_data.get("classroom")
        section = filter_form.cleaned_data.get("section")
        qs = Student.objects.select_related("user", "classroom", "section", "academic_year")
        qs = qs.filter(academic_year=from_year)
        if classroom:
            qs = qs.filter(classroom=classroom)
        if section:
            qs = qs.filter(section=section)
        students = list(qs.order_by("classroom__name", "section__name", "roll_number"))
        for s in students:
            suggested_class = _suggest_promoted_class(s.classroom, to_year)
            is_final = suggested_class is None
            preview_rows.append(
                {
                    "student": s,
                    "suggested_class": suggested_class,
                    "suggested_section": s.section,
                    "is_final_class": is_final,
                }
            )

    if request.method == "POST":
        selected_ids = [int(x) for x in request.POST.getlist("student_ids") if str(x).isdigit()]
        from_year_id = request.POST.get("from_year")
        to_year_id = request.POST.get("to_year")
        if not from_year_id or not to_year_id:
            messages.error(request, "Select both source and target academic years.")
            return redirect("core:school_promote_students")
        from_year = AcademicYear.objects.filter(id=from_year_id).first()
        to_year = AcademicYear.objects.filter(id=to_year_id).first()
        if not from_year or not to_year:
            messages.error(request, "Invalid academic year selection.")
            return redirect("core:school_promote_students")
        if not selected_ids:
            messages.error(request, "Select at least one student.")
            return redirect(f"{reverse('core:school_promote_students')}?from_year={from_year.id}&to_year={to_year.id}")
        action = request.POST.get("action") or StudentPromotion.Action.PROMOTE
        target_class_id = request.POST.get("target_classroom")
        target_section_id = request.POST.get("target_section")
        target_class = ClassRoom.objects.filter(id=target_class_id).first() if target_class_id else None
        target_section = Section.objects.filter(id=target_section_id).first() if target_section_id else None
        if target_section and target_class and not target_class.sections.filter(id=target_section.id).exists():
            messages.error(request, "Target section must belong to selected target class.")
            return redirect(f"{reverse('core:school_promote_students')}?from_year={from_year.id}&to_year={to_year.id}")

        to_process = list(Student.objects.filter(id__in=selected_ids).select_related("classroom", "section", "academic_year"))
        applied = 0
        skipped = 0
        with transaction.atomic():
            for student in to_process:
                if student.academic_year_id == to_year.id:
                    skipped += 1
                    continue
                if StudentPromotion.objects.filter(student=student, from_year=from_year, to_year=to_year).exists():
                    skipped += 1
                    continue

                from_class = student.classroom
                from_section = student.section
                new_class = student.classroom
                new_section = student.section
                final_class = False
                if action == StudentPromotion.Action.PROMOTE:
                    suggested = _suggest_promoted_class(student.classroom, to_year)
                    if suggested is None:
                        final_class = True
                        skipped += 1
                        continue
                    new_class = suggested
                elif action == StudentPromotion.Action.DEMOTE:
                    cur_no = _extract_grade_num(student.classroom.name if student.classroom else "")
                    wanted = (cur_no - 1) if cur_no else None
                    fallback = None
                    if wanted:
                        for c in ClassRoom.objects.filter(academic_year=to_year).order_by("name"):
                            if _extract_grade_num(c.name) == wanted:
                                fallback = c
                                break
                    if fallback:
                        new_class = fallback
                elif action == StudentPromotion.Action.TRANSFER:
                    if target_class:
                        new_class = target_class
                    if target_section:
                        new_section = target_section
                elif action == StudentPromotion.Action.DETAIN:
                    # same class/section but move to next year
                    pass

                if action != StudentPromotion.Action.TRANSFER and new_class and new_section:
                    if not new_class.sections.filter(id=new_section.id).exists():
                        new_section = new_class.sections.order_by("name").first()

                student.classroom = new_class
                student.section = new_section
                student.academic_year = to_year
                student.save(update_fields=["classroom", "section", "academic_year"])

                StudentEnrollment.objects.filter(student=student, is_current=True).update(is_current=False)
                StudentEnrollment.objects.update_or_create(
                    student=student,
                    academic_year=to_year,
                    defaults={
                        "classroom": new_class,
                        "section": new_section,
                        "status": action,
                        "is_current": True,
                    },
                )
                StudentPromotion.objects.create(
                    student=student,
                    from_class=from_class,
                    to_class=new_class,
                    from_section=from_section,
                    to_section=new_section,
                    from_year=from_year,
                    to_year=to_year,
                    action=action,
                    created_by=request.user,
                )
                applied += 1

        if applied:
            messages.success(request, f"Processed {applied} students successfully.")
        if skipped:
            messages.warning(request, f"Skipped {skipped} students (already promoted / invalid mapping / final class).")
        return redirect(f"{reverse('core:school_promote_students')}?from_year={from_year.id}&to_year={to_year.id}")

    return render(
        request,
        "core/school/promotions/index.html",
        {
            "filter_form": filter_form,
            "action_form": action_form,
            "students": students,
            "preview_rows": preview_rows,
            "from_year": from_year,
            "to_year": to_year,
        },
    )


@admin_required
@feature_required("students")
@require_POST
def school_year_end_promote(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    active_year = get_active_academic_year_obj()
    to_year_id = request.POST.get("to_year")
    to_year = AcademicYear.objects.filter(id=to_year_id).first() if to_year_id else None
    if not active_year or not to_year:
        messages.error(request, "Please select valid active and target academic years.")
        return redirect("core:school_academic_years")

    students = list(Student.objects.filter(academic_year=active_year).select_related("classroom", "section"))
    applied = 0
    skipped = 0
    with transaction.atomic():
        for s in students:
            if s.academic_year_id == to_year.id:
                skipped += 1
                continue
            if StudentPromotion.objects.filter(student=s, from_year=active_year, to_year=to_year).exists():
                skipped += 1
                continue
            next_class = _suggest_promoted_class(s.classroom, to_year)
            if not next_class:
                # final class: detain in same class-name if available in target year
                same_named = ClassRoom.objects.filter(academic_year=to_year, name__iexact=(s.classroom.name if s.classroom else "")).first()
                if not same_named:
                    skipped += 1
                    continue
                next_class = same_named
                action = StudentPromotion.Action.DETAIN
            else:
                action = StudentPromotion.Action.PROMOTE

            next_section = s.section
            if next_section and not next_class.sections.filter(id=next_section.id).exists():
                next_section = next_class.sections.order_by("name").first()

            old_class = s.classroom
            old_section = s.section
            s.classroom = next_class
            s.section = next_section
            s.academic_year = to_year
            s.save(update_fields=["classroom", "section", "academic_year"])
            StudentEnrollment.objects.filter(student=s, is_current=True).update(is_current=False)
            StudentEnrollment.objects.update_or_create(
                student=s,
                academic_year=to_year,
                defaults={"classroom": next_class, "section": next_section, "status": action, "is_current": True},
            )
            StudentPromotion.objects.create(
                student=s,
                from_class=old_class,
                to_class=next_class,
                from_section=old_section,
                to_section=next_section,
                from_year=active_year,
                to_year=to_year,
                action=action,
                created_by=request.user,
            )
            applied += 1
    messages.success(request, f"Year-end process completed. Applied: {applied}, skipped: {skipped}.")
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
    )
    # Newest academic year first; within a year, higher grades first (Grade 10 … Grade 1) using digits in name.
    if connection.vendor == "postgresql":
        meta = ClassRoom._meta
        tbl = connection.ops.quote_name(meta.db_table)
        col = connection.ops.quote_name(meta.get_field("name").column)
        grade_sql = (
            f"CAST(COALESCE(NULLIF(regexp_replace({tbl}.{col}, '[^0-9]', '', 'g'), ''), '0') AS INTEGER)"
        )
        qs = qs.annotate(_class_grade_sort=RawSQL(grade_sql, [])).order_by(
            F("academic_year__start_date").desc(nulls_last=True),
            "-_class_grade_sort",
        )
    else:
        qs = qs.order_by(
            F("academic_year__start_date").desc(nulls_last=True),
            "-name",
        )
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

    qs = Subject.objects.all().order_by("name")
    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(code__icontains=search))
    paginator = Paginator(qs, 15)
    page = request.GET.get("page", 1)
    subjects = paginator.get_page(page)
    return render(request, "core/school/subjects/list.html", {
        "subjects": subjects,
        "filters": {"q": search},
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
# Placeholder / Coming Soon
# ======================

@login_required
def students_list(request):
    return render(request, "core/placeholders/coming_soon.html", {"title": "Students"})

@login_required
def teachers_list(request):
    # Teacher view: school directory of all teachers and their subjects.
    if getattr(request.user, "role", None) == "TEACHER":
        school = getattr(request.user, "school", None)
        if not school:
            return render(
                request,
                "core/teacher/teachers_directory.html",
                {"teachers_rows": [], "no_school": True},
            )
        teachers_qs = (
            Teacher.objects.filter(user__school=school)
            .select_related("user")
            .prefetch_related(
                "subjects",
                "classrooms",
                Prefetch(
                    "class_section_subject_teacher_mappings",
                    queryset=ClassSectionSubjectTeacher.objects.select_related(
                        "subject", "class_obj", "section"
                    ),
                ),
            )
            .order_by("user__first_name", "user__last_name", "id")
        )
        teachers_rows = []
        for t in teachers_qs:
            subj_names = {s.name for s in t.subjects.all()}
            grade_names = set()
            for c in t.classrooms.all():
                if c.name:
                    grade_names.add(c.name.strip())
            for m in t.class_section_subject_teacher_mappings.all():
                if m.subject_id:
                    subj_names.add(m.subject.name)
                if m.class_obj and m.class_obj.name:
                    grade_names.add(m.class_obj.name.strip())
            teachers_rows.append(
                {
                    "teacher": t,
                    "subjects_display": ", ".join(sorted(subj_names)) if subj_names else "—",
                    "grades_display": ", ".join(sorted(grade_names)) if grade_names else "—",
                }
            )
        return render(
            request,
            "core/teacher/teachers_directory.html",
            {"teachers_rows": teachers_rows, "no_school": False},
        )

    # Student view: show only teachers/subjects relevant to student's class+section.
    if getattr(request.user, "role", None) == "STUDENT":
        student = getattr(request.user, "student_profile", None)
        if not student or not student.classroom:
            return render(request, "core/student/teachers_list.html", {"assignments": []})

        section_name = student.section.name if student.section else "N/A"
        class_name = student.classroom.name
        if not student.section:
            assignments = []
        else:
            mappings = (
                ClassSectionSubjectTeacher.objects.filter(
                    class_obj=student.classroom,
                    section=student.section,
                )
                .select_related("subject", "teacher__user")
                .order_by("subject__name", "teacher__user__first_name")
            )
            assignments = [
                {
                    "teacher": m.teacher,
                    "subject": m.subject.name,
                    "class_name": class_name,
                    "section_name": section_name,
                }
                for m in mappings
            ]
        return render(
            request,
            "core/student/teachers_list.html",
            {"assignments": assignments},
        )

    return render(request, "core/placeholders/coming_soon.html", {"title": "Teachers"})

@login_required
@feature_required("attendance")
def attendance_list(request):
    """School admin: mark and view student attendance by class, section, and date."""
    if getattr(request.user, "role", None) != User.Roles.ADMIN:
        return HttpResponseForbidden("School admin access required.")
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    if not has_feature_access(school, "attendance", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")

    # Recover from a poisoned connection (e.g. prior query error in same request/thread).
    try:
        if getattr(connection, "needs_rollback", False):
            connection.rollback()
    except Exception:
        pass

    today = date.today()
    classrooms = list(
        ClassRoom.objects.prefetch_related("sections").order_by("academic_year", "name")
    )
    class_sections_data = [
        {
            "id": c.id,
            "name": c.name,
            "sections": [{"id": s.id, "name": s.name} for s in c.sections.all().order_by("name")],
        }
        for c in classrooms
    ]

    def _parse_int(val):
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    def _redirect_with_params(cid, sid, d_str, q_str, focus_sid=None):
        params = {"attendance_date": d_str}
        if cid:
            params["classroom_id"] = cid
        if sid:
            params["section_id"] = sid
        if q_str:
            params["q"] = q_str
        if focus_sid:
            params["student_id"] = focus_sid
        return redirect(reverse("core:attendance_list") + "?" + urlencode(params))

    if request.method == "POST":
        classroom_id = _parse_int(request.POST.get("classroom_id"))
        section_id = _parse_int(request.POST.get("section_id"))
        date_str = request.POST.get("attendance_date", "").strip()
        search_q = request.POST.get("q", "").strip()
        post_focus_sid = _parse_int(request.POST.get("student_id"))

        if not classroom_id or not section_id or not date_str:
            messages.error(request, "Please select class, section, and date before saving.")
            return redirect("core:attendance_list")

        try:
            att_date = date.fromisoformat(date_str)
        except (ValueError, TypeError):
            messages.error(request, "Invalid attendance date.")
            return redirect("core:attendance_list")

        if att_date > today:
            messages.error(request, "Cannot mark attendance for a future date.")
            return _redirect_with_params(classroom_id, section_id, date_str, search_q, post_focus_sid)

        classroom = ClassRoom.objects.filter(pk=classroom_id).first()
        if not classroom or not classroom.sections.filter(pk=section_id).exists():
            messages.error(request, "Invalid class or section combination.")
            return redirect("core:attendance_list")

        try:
            stud_qs = (
                Student.objects.filter(classroom_id=classroom_id, section_id=section_id)
                .select_related("user")
                .defer("academic_year")
                .order_by("roll_number")
            )
        except (ProgrammingError, InternalError, DatabaseError):
            try:
                connection.rollback()
            except Exception:
                pass
            messages.error(
                request,
                "Student list could not be loaded (database schema may be outdated). Run "
                f"{tenant_migrate_cli_hint(school)} then refresh.",
            )
            return _redirect_with_params(classroom_id, section_id, date_str, search_q, post_focus_sid)
        if search_q:
            sq = search_q.strip()
            stud_qs = stud_qs.filter(
                Q(user__first_name__icontains=sq)
                | Q(user__last_name__icontains=sq)
                | Q(user__username__icontains=sq)
                | Q(admission_number__icontains=sq)
                | Q(roll_number__icontains=sq)
            )
        students = list(stud_qs)

        if not students:
            messages.error(request, "No students match the current filters.")
            return _redirect_with_params(classroom_id, section_id, date_str, search_q, post_focus_sid)

        valid_status = set(Attendance.Status.values)
        active_ay = get_active_academic_year_obj()

        try:
            with transaction.atomic():
                for student in students:
                    status = request.POST.get(f"status_{student.id}", Attendance.Status.PRESENT)
                    if status not in valid_status:
                        status = Attendance.Status.PRESENT
                    defaults = {
                        "status": status,
                        "marked_by": request.user,
                    }
                    if active_ay is not None:
                        defaults["academic_year"] = active_ay
                    Attendance.objects.update_or_create(
                        student=student,
                        date=att_date,
                        defaults=defaults,
                    )
        except ProgrammingError as exc:
            try:
                connection.rollback()
            except Exception:
                pass
            err = (str(exc) or "").lower()
            if "academic_year" in err or "column" in err or "does not exist" in err:
                messages.error(
                    request,
                    "Attendance could not be saved: this school’s database schema is missing newer columns "
                    "(for example attendance.academic_year). From the project root, run: "
                    f"{tenant_migrate_cli_hint(school)}",
                )
            else:
                messages.error(
                    request,
                    "Attendance could not be saved due to a database error. If this persists, run "
                    f"{tenant_migrate_cli_hint(school)} and try again.",
                )
            return _redirect_with_params(classroom_id, section_id, date_str, search_q, post_focus_sid)
        except (InternalError, DatabaseError):
            # Do not call rollback() inside atomic(); outer handler after block exit.
            try:
                connection.rollback()
            except Exception:
                pass
            messages.error(
                request,
                "Attendance could not be saved (database error—often a failed transaction or outdated schema). "
                f"Run {tenant_migrate_cli_hint(school)} then try again.",
            )
            return _redirect_with_params(classroom_id, section_id, date_str, search_q, post_focus_sid)

        messages.success(
            request,
            f"Attendance saved for {len(students)} student(s) on {att_date}.",
        )
        return _redirect_with_params(classroom_id, section_id, date_str, search_q, post_focus_sid)

    # GET
    focus_student = None
    focus_student_id = _parse_int(request.GET.get("student_id"))
    if focus_student_id:
        focus_student = (
            Student.objects.select_related("user", "classroom", "section")
            .filter(pk=focus_student_id)
            .first()
        )

    classroom_id = _parse_int(request.GET.get("classroom_id"))
    section_id = _parse_int(request.GET.get("section_id"))
    date_str = request.GET.get("attendance_date", today.isoformat()).strip()
    search_q = request.GET.get("q", "").strip()

    if focus_student and focus_student.classroom_id and focus_student.section_id:
        classroom_id = focus_student.classroom_id
        section_id = focus_student.section_id
        if not search_q:
            search_q = (
                (focus_student.admission_number or "").strip()
                or (focus_student.roll_number or "").strip()
                or focus_student.user.username
                or ""
            )
    elif focus_student_id:
        if not focus_student:
            messages.error(request, "Student not found.")
        elif not focus_student.classroom_id or not focus_student.section_id:
            messages.warning(
                request,
                "This student has no class or section assigned. Assign class and section before recording attendance.",
            )
        focus_student = None

    try:
        att_date = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        att_date = today
        date_str = today.isoformat()

    future_date = att_date > today

    students_with_status = []
    section_valid_for_class = True
    if classroom_id and section_id:
        classroom = ClassRoom.objects.filter(pk=classroom_id).first()
        if not classroom:
            section_valid_for_class = False
        elif not classroom.sections.filter(pk=section_id).exists():
            section_valid_for_class = False
        else:
            try:
                stud_qs = (
                    Student.objects.filter(classroom_id=classroom_id, section_id=section_id)
                    .select_related("user")
                    .defer("academic_year")
                    .order_by("roll_number")
                )
            except (ProgrammingError, InternalError, DatabaseError):
                try:
                    connection.rollback()
                except Exception:
                    pass
                stud_qs = Student.objects.none()
            if search_q:
                sq = search_q.strip()
                stud_qs = stud_qs.filter(
                    Q(user__first_name__icontains=sq)
                    | Q(user__last_name__icontains=sq)
                    | Q(user__username__icontains=sq)
                    | Q(admission_number__icontains=sq)
                    | Q(roll_number__icontains=sq)
                )
            students = list(stud_qs)
            if students:
                try:
                    att_map = {
                        a.student_id: a.status
                        for a in Attendance.objects.filter(
                            student__in=students,
                            date=att_date,
                        ).defer("academic_year")
                    }
                except (ProgrammingError, InternalError, DatabaseError):
                    try:
                        connection.rollback()
                    except Exception:
                        pass
                    att_map = {}
                students_with_status = [
                    {
                        "student": s,
                        "status": att_map.get(s.id, Attendance.Status.PRESENT),
                    }
                    for s in students
                ]

    return render(
        request,
        "core/school/attendance_mark.html",
        {
            "class_sections_data": class_sections_data,
            "classroom_id": classroom_id,
            "section_id": section_id,
            "attendance_date": date_str,
            "search_q": search_q,
            "students_with_status": students_with_status,
            "future_date": future_date,
            "section_valid_for_class": section_valid_for_class,
            "status_choices": Attendance.Status.choices,
            "focus_student": focus_student,
        },
    )

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
    if not has_feature_access(getattr(request.user, "school", None), "homework", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")

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
        mapped_subject_ids = list(
            ClassSectionSubjectTeacher.objects.filter(
                class_obj=student.classroom,
                section=student.section,
            ).values_list("subject_id", flat=True)
        )
        # Legacy: homework tied to a subject assigned to this class+section
        hw_legacy = Homework.objects.filter(
            subject_id__in=mapped_subject_ids,
        ).select_related("subject", "teacher", "teacher__user")
        hw_ids_legacy = set(hw_legacy.values_list("id", flat=True))
        hw_new = [h for h in hw_class_section if h.id not in hw_ids_legacy]
        assignments_raw = list(hw_legacy) + hw_new
        assignments_raw.sort(key=lambda h: (h.due_date, -h.id))

        subject_qs = Subject.objects.filter(id__in=mapped_subject_ids).order_by("name")
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

    if getattr(request.user, "role", None) == "TEACHER":
        teacher = getattr(request.user, "teacher_profile", None)
        qs = _homework_queryset_for_teacher(teacher, request.user)
        classroom_filter = request.GET.get("classroom", "").strip()
        if classroom_filter.isdigit():
            qs = qs.filter(classes__id=int(classroom_filter))
        homework_list = qs.distinct()
        class_ids = list(
            ClassSectionSubjectTeacher.objects.filter(teacher=teacher).values_list(
                "class_obj_id", flat=True
            ).distinct()
        ) if teacher else []
        classrooms = list(ClassRoom.objects.filter(id__in=class_ids).order_by("name"))
        return render(
            request,
            "core/teacher/homework_list.html",
            {
                "homework_list": homework_list,
                "classrooms": classrooms,
                "filters": {"classroom": classroom_filter},
                "today": date.today(),
            },
        )

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
        if not student.section_id:
            raise PermissionDenied
        allowed = ClassSectionSubjectTeacher.objects.filter(
            class_obj_id=student.classroom_id,
            section_id=student.section_id,
            subject_id=homework.subject_id,
        ).exists()
        if not allowed:
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
    if not has_feature_access(getattr(request.user, "school", None), "reports", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    return render(request, "core/placeholders/coming_soon.html", {"title": "Reports"})


# ======================
# Teacher Actions
# ======================

@teacher_required
def teacher_students_list(request):
    """Students in class–sections this teacher is assigned to (via subject mapping)."""
    school = request.user.school
    teacher = getattr(request.user, "teacher_profile", None)
    students = []
    pairs = []
    class_options = []
    section_options = []
    if school and teacher:
        from .utils import teacher_class_section_pairs_display

        pairs = teacher_class_section_pairs_display(teacher)
        q = Q()
        for cn, sn in pairs:
            q |= Q(classroom__name__iexact=cn, section__name__iexact=sn)
        pairs_csst = (
            ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
            .values_list("class_obj_id", "section_id")
            .distinct()
        )
        for cid, sid in pairs_csst:
            if cid and sid:
                q |= Q(classroom_id=cid, section_id=sid)
        if q:
            qs = (
                Student.objects.filter(q)
                .select_related("user", "classroom", "section")
                .distinct()
            )
            class_name = (request.GET.get("class_name") or "").strip()
            section_name = (request.GET.get("section") or "").strip()
            search = (request.GET.get("q") or "").strip()
            if class_name:
                qs = qs.filter(classroom__name__iexact=class_name)
            if section_name:
                qs = qs.filter(section__name__iexact=section_name)
            if search:
                qs = qs.filter(
                    Q(user__first_name__icontains=search)
                    | Q(user__last_name__icontains=search)
                    | Q(user__username__icontains=search)
                )
            students = list(
                qs.order_by("classroom__name", "section__name", "roll_number", "user__first_name")
            )
        class_options = sorted({c for c, _ in pairs if c})
        sel_class = (request.GET.get("class_name") or "").strip()
        if sel_class:
            section_options = sorted({s for c, s in pairs if c.lower() == sel_class.lower()})
        else:
            section_options = sorted({s for _, s in pairs if s})
    return render(
        request,
        "core/teacher/students_list.html",
        {
            "students": students,
            "filters": {
                "class_name": (request.GET.get("class_name") or "").strip(),
                "section": (request.GET.get("section") or "").strip(),
                "q": (request.GET.get("q") or "").strip(),
            },
            "class_options": class_options,
            "section_options": section_options,
        },
    )


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
            hw.teacher = teacher
            hw.save()
            form.save_m2m()
            messages.success(request, "Homework created successfully.")
            return redirect("core:homework_list")
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

        mapped_subject_ids = set(
            ClassSectionSubjectTeacher.objects.filter(teacher=teacher).values_list("subject_id", flat=True).distinct()
        )
        mapped_subject_ids.update(teacher.subjects.values_list("id", flat=True))
        if teacher.subject_id:
            mapped_subject_ids.add(teacher.subject_id)
        form.fields["subject"].queryset = Subject.objects.filter(id__in=mapped_subject_ids).order_by("name")

    return render(request, "core/teacher/marks_form.html", {"form": form, "title": "Enter Marks"})


def _teacher_allowed_class_section_pairs(teacher):
    """Return allowed (class_name, section_name) pairs for this teacher (lowercased)."""
    from .utils import teacher_allowed_class_section_pairs_lower

    return teacher_allowed_class_section_pairs_lower(teacher)


def _teacher_exam_access(exam, school, teacher):
    """
    Teacher may access an exam if:
    - exam.teacher is set and matches this teacher, OR
    - exam.teacher is unset and they teach this exam's subject in that class–section, OR
    - exam has no subject (legacy): class–section is in their mapping scope.
    If another teacher is assigned (exam.teacher), only that teacher may access.
    """
    if exam is None or not teacher:
        return False
    if getattr(exam, "teacher_id", None):
        return exam.teacher_id == teacher.id
    if not exam.class_name or not exam.section:
        return False
    if getattr(exam, "subject_id", None):
        if ClassSectionSubjectTeacher.objects.filter(
            teacher=teacher,
            class_obj__name__iexact=exam.class_name,
            section__name__iexact=exam.section,
            subject_id=exam.subject_id,
        ).exists():
            return True
        pair_ok = (exam.class_name.lower(), exam.section.lower()) in _teacher_allowed_class_section_pairs(teacher)
        subj_ids = set(teacher.subjects.values_list("id", flat=True))
        if teacher.subject_id:
            subj_ids.add(teacher.subject_id)
        return pair_ok and exam.subject_id in subj_ids
    allowed_pairs = _teacher_allowed_class_section_pairs(teacher)
    return (exam.class_name.lower(), exam.section.lower()) in allowed_pairs


def _teacher_exam_session_access(session_obj, school, teacher):
    """True if teacher may view this session (any paper passes _teacher_exam_access)."""
    if not session_obj or not teacher:
        return False
    for paper in Exam.objects.filter(session=session_obj).select_related("teacher"):
        if _teacher_exam_access(paper, school, teacher):
            return True
    return False


@teacher_required
@feature_required("exams")
def teacher_exams(request):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")
    teacher = getattr(request.user, "teacher_profile", None)
    if teacher:
        qs = (
            Exam.objects.filter(_teacher_visible_exam_q(teacher))
            .distinct()
            .select_related("session", "session__created_by", "subject", "teacher__user")
        )
    else:
        qs = Exam.objects.none()

    papers = list(qs.order_by("-session_id", "-date", "subject__name"))
    session_groups = []
    seen_session = set()
    standalone_papers = []
    for p in papers:
        if p.session_id:
            if p.session_id in seen_session:
                continue
            seen_session.add(p.session_id)
            sess = p.session
            if not sess:
                continue
            sess_papers = [x for x in papers if x.session_id == p.session_id]
            sess_papers.sort(key=lambda x: (x.date or date.min, x.subject_id or 0))
            # Papers are already limited to this teacher via _teacher_visible_exam_q; do not
            # gate again on _teacher_exam_session_access (can drop rows if checks diverge).
            if sess_papers:
                dts = [x.date for x in sess_papers if x.date]
                session_groups.append({
                    "session": sess,
                    "papers": sess_papers,
                    "date_min": min(dts) if dts else None,
                    "date_max": max(dts) if dts else None,
                })
        else:
            standalone_papers.append(p)

    session_groups.sort(key=lambda g: (g["session"].created_at, g["session"].pk), reverse=True)
    standalone_papers.sort(key=lambda x: (x.date or date.min), reverse=True)

    return render(
        request,
        "core/teacher/exams.html",
        {
            "exam_session_groups": session_groups,
            "standalone_papers": standalone_papers,
        },
    )


@teacher_required
@feature_required("exams")
def teacher_exam_session_detail(request, session_id):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")
    if not has_feature_access(school, "exams", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    teacher = getattr(request.user, "teacher_profile", None)
    session_obj = get_object_or_404(
        ExamSession.objects.select_related("classroom", "created_by"),
        pk=session_id,
    )
    if not _teacher_exam_session_access(session_obj, school, teacher):
        raise PermissionDenied
    papers = list(
        _exam_papers_full_qs()
        .filter(session=session_obj)
        .order_by("date", "subject__name")
    )
    papers = [p for p in papers if _teacher_exam_access(p, school, teacher)]
    return render(
        request,
        "core/teacher/exam_session_detail.html",
        {
            "session": session_obj,
            "papers": papers,
        },
    )


@admin_required
@feature_required("exams")
def school_exams_list(request):
    """
    School Admin: exam sessions (multi-subject papers).
    Filter by class, section, teacher, subject, search.
    """
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")

    class_id = request.GET.get("classroom") or ""
    section_id = request.GET.get("section") or ""
    teacher_id = request.GET.get("teacher") or ""
    subject_id = request.GET.get("subject") or ""
    q = (request.GET.get("q") or "").strip()

    # Recover if a previous DB error left the connection in aborted state.
    try:
        if getattr(connection, "needs_rollback", False):
            connection.rollback()
    except Exception:
        pass

    exam_sessions_enabled = True
    try:
        session_qs = (
            ExamSession.objects.select_related("created_by", "classroom")
            .annotate(
                paper_count=Count("papers", distinct=True),
                date_min=Min("papers__date"),
                date_max=Max("papers__date"),
            )
            .order_by("-created_at", "-id", "name", "class_name", "section")
        )

        if class_id:
            try:
                classroom_obj = ClassRoom.objects.get(id=class_id)
                session_qs = session_qs.filter(class_name=classroom_obj.name)
            except ClassRoom.DoesNotExist:
                session_qs = ExamSession.objects.none()

        if section_id:
            try:
                sec = Section.objects.get(id=section_id)
                session_qs = session_qs.filter(section__iexact=sec.name)
            except Section.DoesNotExist:
                session_qs = ExamSession.objects.none()

        if teacher_id.isdigit():
            session_qs = session_qs.filter(papers__teacher_id=int(teacher_id)).distinct()

        if subject_id.isdigit():
            session_qs = session_qs.filter(papers__subject_id=int(subject_id)).distinct()

        if q:
            session_qs = session_qs.filter(name__icontains=q)

        exam_sessions = list(session_qs)
    except (ProgrammingError, InternalError, DatabaseError):
        # Tenant schema has not run the ExamSession/session_id migration yet.
        exam_sessions_enabled = False
        exam_sessions = []
        try:
            connection.rollback()
        except Exception:
            pass

    try:
        if getattr(connection, "needs_rollback", False):
            connection.rollback()
    except Exception:
        pass

    try:
        classrooms = ClassRoom.objects.all().order_by("name")
        sections = Section.objects.all().order_by("name")
        teachers = (
            Teacher.objects.filter(user__school=school).select_related("user").order_by(
                "user__first_name", "user__last_name"
            )
            if school
            else Teacher.objects.none()
        )
        subjects = Subject.objects.all().order_by("name")
    except (ProgrammingError, InternalError, DatabaseError):
        try:
            connection.rollback()
        except Exception:
            pass
        classrooms = ClassRoom.objects.none()
        sections = Section.objects.none()
        teachers = Teacher.objects.none()
        subjects = Subject.objects.none()

    return render(
        request,
        "core/school/exams_list.html",
        {
            "exam_sessions": exam_sessions,
            "exam_sessions_enabled": exam_sessions_enabled,
            "classrooms": classrooms,
            "sections": sections,
            "teachers": teachers,
            "subjects": subjects,
            "filters": {
                "classroom": class_id,
                "section": section_id,
                "teacher": teacher_id,
                "subject": subject_id,
                "q": q,
            },
        },
    )


@admin_required
@feature_required("exams")
def school_exam_session_detail(request, session_id):
    """Admin: papers (subjects + dates) under one exam session."""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")

    session_obj = get_object_or_404(
        ExamSession.objects.select_related("created_by", "classroom"),
        pk=session_id,
    )
    papers = list(
        _exam_papers_full_qs()
        .filter(session=session_obj)
        .order_by("date", "subject__name")
    )
    return render(
        request,
        "core/school/exam_session_detail.html",
        {
            "session": session_obj,
            "papers": papers,
        },
    )


def _exam_teacher_for_school(school, teacher_id):
    if not teacher_id:
        return None
    return Teacher.objects.filter(id=teacher_id, user__school=school).first()


def _default_teacher_for_class_section_subject(school, classroom, class_name, section_name, subject):
    """
    If exactly one teacher is mapped to this class, section, and subject in the school, return them.
    Otherwise None (subject teachers resolve via ClassSectionSubjectTeacher; no global exam owner).
    """
    if not subject or not school:
        return None
    sn = (section_name or "").strip()
    qs = ClassSectionSubjectTeacher.objects.filter(
        subject=subject,
        teacher__user__school=school,
        section__name__iexact=sn,
    )
    if classroom is not None:
        qs = qs.filter(class_obj=classroom)
    elif class_name:
        qs = qs.filter(class_obj__name__iexact=(class_name or "").strip())
    else:
        return None
    qs = qs.select_related("teacher")
    if qs.count() == 1:
        return qs.first().teacher
    return None


def _teacher_visible_exam_q(teacher):
    """Exams this teacher may see: assigned to them, or unassigned and they teach that subject in that class–section."""
    from .utils import teacher_class_section_pairs_display

    q = Q(teacher=teacher)
    for row in ClassSectionSubjectTeacher.objects.filter(teacher=teacher).select_related(
        "class_obj", "section"
    ):
        cname = (row.class_obj.name if row.class_obj_id else "") or ""
        sname = (row.section.name if row.section_id else "") or ""
        if not cname or not sname or not row.subject_id:
            continue
        q |= Q(
            teacher__isnull=True,
            class_name__iexact=cname,
            section__iexact=sname,
            subject_id=row.subject_id,
        )
    # Profile subjects + class/section from CSST or Teacher.classrooms × class.sections
    subj_ids = set(teacher.subjects.values_list("id", flat=True))
    if teacher.subject_id:
        subj_ids.add(teacher.subject_id)
    for cname, sname in teacher_class_section_pairs_display(teacher):
        if not cname or not sname:
            continue
        for sid in subj_ids:
            q |= Q(
                teacher__isnull=True,
                class_name__iexact=cname,
                section__iexact=sname,
                subject_id=sid,
            )
    return q


def _exam_duplicate(class_name, section_name, dt, subject, exclude_pk=None):
    """Block duplicate (class, section, date, subject) when subject is set."""
    qs = Exam.objects.filter(class_name=class_name, section=section_name, date=dt)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if subject is not None:
        qs = qs.filter(subject_id=subject.id)
    else:
        qs = qs.filter(subject__isnull=True)
    return qs.exists()


def _exam_class_section_date_conflict(class_name, section_name, dt, exclude_pk=None):
    """True if another exam exists for the same class, section, and date (any subject)."""
    qs = Exam.objects.filter(class_name=class_name, section__iexact=section_name, date=dt)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def _exam_teacher_date_conflict(teacher_id, dt, exclude_pk=None):
    if not teacher_id:
        return False
    qs = Exam.objects.filter(teacher_id=teacher_id, date=dt)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def _exam_read_qs():
    """
    Exam rows without loading start_time/end_time (omits columns from SELECT).
    Use until migration 0017_exam_start_end_time is applied on the schema.
    """
    # Defer newly added columns for tenant schemas that may lag migrations,
    # preventing SELECT on missing columns (e.g., session_id, academic_year_id).
    return Exam.objects.defer("start_time", "end_time", "session", "academic_year")


def _exam_papers_full_qs():
    """Exam papers with session and times for schedule / detail screens."""
    return Exam.objects.select_related("subject", "teacher__user", "created_by", "session")


def _parse_exam_calendar_date(value):
    """Parse FullCalendar startStr / API payload to a date."""
    from datetime import datetime as _dt

    if not value:
        raise ValueError("missing date")
    s = str(value).strip()
    if "T" in s:
        s = s.split("T", 1)[0]
    return _dt.strptime(s[:10], "%Y-%m-%d").date()


def _exam_events_queryset(request):
    """
    Exams visible to the current user for the calendar API.
    Admin: all (optional filters). Teacher: assigned or in mapped class+section. Student: own class+section.
    """
    role = getattr(request.user, "role", None)
    qs = (
        _exam_read_qs()
        .filter(date__isnull=False)
        .exclude(section="")
        .select_related("subject", "teacher__user")
    )

    if role == "ADMIN":
        pass
    elif role == "TEACHER":
        teacher = getattr(request.user, "teacher_profile", None)
        if teacher:
            # Same rules as teacher_exams / marks: assigned paper OR unassigned + mapped subject
            qs = qs.filter(_teacher_visible_exam_q(teacher)).distinct()
        else:
            qs = Exam.objects.none()
    elif role == "STUDENT":
        st = getattr(request.user, "student_profile", None)
        if st and st.classroom:
            sn = st.section.name if st.section else ""
            qs = qs.filter(class_name=st.classroom.name, section__iexact=sn)
        else:
            qs = Exam.objects.none()
    else:
        qs = Exam.objects.none()

    class_id = request.GET.get("classroom") or ""
    if class_id.isdigit():
        try:
            classroom_obj = ClassRoom.objects.get(id=int(class_id))
            qs = qs.filter(class_name=classroom_obj.name)
        except ClassRoom.DoesNotExist:
            qs = qs.none()

    section_id = request.GET.get("section") or ""
    if section_id.isdigit():
        try:
            sec = Section.objects.get(id=int(section_id))
            qs = qs.filter(section__iexact=sec.name)
        except Section.DoesNotExist:
            qs = qs.none()

    if role == "ADMIN":
        teacher_id = request.GET.get("teacher") or ""
        if teacher_id.isdigit():
            qs = qs.filter(teacher_id=int(teacher_id))

    subject_id = request.GET.get("subject") or ""
    if subject_id.isdigit():
        qs = qs.filter(subject_id=int(subject_id))

    return qs.order_by("date", "id")


_EXAM_CALENDAR_COLORS = (
    "#2e7d32",
    "#1565c0",
    "#6a1b9a",
    "#c62828",
    "#ef6c00",
    "#00838f",
    "#4527a0",
    "#558b2f",
)


def _exam_event_color(exam):
    if exam.subject_id:
        return _EXAM_CALENDAR_COLORS[exam.subject_id % len(_EXAM_CALENDAR_COLORS)]
    return "#607d8b"


def _exam_to_fc_event(exam):
    from datetime import datetime as _dt

    color = _exam_event_color(exam)
    subj_name = exam.subject.name if exam.subject else "General"
    t_name = ""
    if exam.teacher_id and exam.teacher:
        t_name = exam.teacher.user.get_full_name() or exam.teacher.user.username
    title = f"{subj_name} ({exam.class_name} · {exam.section})"
    ext = {
        "exam_name": exam.name,
        "subject": subj_name,
        "class_name": exam.class_name,
        "section": exam.section,
        "teacher": t_name or "—",
    }
    ev = {
        "id": str(exam.pk),
        "title": title[:120],
        "backgroundColor": color,
        "borderColor": color,
        "textColor": "#ffffff",
        "extendedProps": ext,
    }
    # Avoid loading deferred time fields (or missing DB columns before migration 0017).
    deferred = exam.get_deferred_fields()
    if "start_time" not in deferred and "end_time" not in deferred and exam.start_time and exam.end_time:
        start = _dt.combine(exam.date, exam.start_time)
        end = _dt.combine(exam.date, exam.end_time)
        ev["start"] = start.isoformat(timespec="minutes")
        ev["end"] = end.isoformat(timespec="minutes")
        ev["allDay"] = False
    else:
        ev["start"] = exam.date.isoformat()
        ev["allDay"] = True
    return ev


@admin_required
@feature_required("exams")
def school_exam_create(request):
    """
    School Admin: single exam (one subject, one date) or bulk schedule
    (multiple subjects on consecutive days within a date range).
    """
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")

    from .forms import SchoolExamSingleForm, SchoolExamSchedulerForm

    single_form = SchoolExamSingleForm(school)
    scheduler_form = SchoolExamSchedulerForm(school)

    if request.method == "POST":
        mode = request.POST.get("create_mode") or "single"
        if mode == "multiple":
            scheduler_form = SchoolExamSchedulerForm(school, request.POST)
            single_form = SchoolExamSingleForm(school)
            if scheduler_form.is_valid():
                class_ids = [int(x) for x in request.POST.getlist("classes") if str(x).isdigit()]
                section_ids = [int(x) for x in request.POST.getlist("sections") if str(x).isdigit()]
                subject_ids = [int(x) for x in request.POST.getlist("subject_include") if str(x).isdigit()]
                tm = scheduler_form.cleaned_data["total_marks"]
                exam_name_base = (scheduler_form.cleaned_data.get("exam_name") or "").strip()

                if not class_ids:
                    messages.error(request, "Select at least one class.")
                elif not section_ids:
                    messages.error(request, "Select at least one section.")
                elif not subject_ids:
                    messages.error(request, "Select at least one subject and set a date for each (use the checkboxes).")
                else:
                    missing_dates = []
                    for sid in subject_ids:
                        if not (request.POST.get(f"exam_date_{sid}") or "").strip():
                            missing_dates.append(sid)
                    if missing_dates:
                        messages.error(
                            request,
                            "Every checked subject must have an exam date.",
                        )
                    else:
                        classrooms = {
                            c.id: c
                            for c in ClassRoom.objects.filter(id__in=class_ids).prefetch_related("sections")
                        }
                        sections_by_id = {s.id: s for s in Section.objects.filter(id__in=section_ids)}
                        pair_items = []
                        for cid in class_ids:
                            c = classrooms.get(cid)
                            if not c:
                                continue
                            allowed_sec_ids = set(c.sections.values_list("id", flat=True))
                            for sid in section_ids:
                                if sid not in allowed_sec_ids:
                                    continue
                                sec = sections_by_id.get(sid)
                                if c and sec:
                                    pair_items.append((c, sec.name))

                        pair_items.sort(key=lambda x: (x[0].name, x[1]))
                        if not pair_items:
                            messages.error(
                                request,
                                "No valid class–section combinations. Each section must belong to a selected class.",
                            )
                        else:
                            subjects_by_id = {s.id: s for s in Subject.objects.filter(id__in=subject_ids)}
                            papers_created = 0
                            sessions_created = 0
                            skipped = []
                            with transaction.atomic():
                                for classroom, sn in pair_items:
                                    cn = classroom.name
                                    session = ExamSession.objects.create(
                                        name=exam_name_base[:100],
                                        class_name=cn,
                                        section=sn,
                                        classroom=classroom,
                                        created_by=request.user,
                                    )
                                    sessions_created += 1
                                    for subj_id in subject_ids:
                                        subj = subjects_by_id.get(subj_id)
                                        if not subj:
                                            skipped.append(f"Unknown subject id {subj_id}")
                                            continue
                                        raw = request.POST.get(f"exam_date_{subj_id}", "").strip()
                                        try:
                                            dt = date.fromisoformat(raw)
                                        except (ValueError, TypeError):
                                            skipped.append(f"{subj.name}: invalid date")
                                            continue
                                        if _exam_class_section_date_conflict(cn, sn, dt):
                                            skipped.append(
                                                f"{cn} {sn} {dt} ({subj.name}) — class already has an exam that day"
                                            )
                                            continue
                                        if _exam_duplicate(cn, sn, dt, subj):
                                            skipped.append(
                                                f"{cn} {sn} {dt} ({subj.name}) — duplicate exam"
                                            )
                                            continue
                                        paper_name = subj.name[:100]
                                        raw_tid = (request.POST.get(f"exam_teacher_{subj_id}") or "").strip()
                                        chosen_teacher = None
                                        if raw_tid.isdigit():
                                            chosen_teacher = _exam_teacher_for_school(school, int(raw_tid))
                                        paper_teacher = chosen_teacher or _default_teacher_for_class_section_subject(
                                            school, classroom, cn, sn, subj
                                        )
                                        Exam.objects.create(
                                            session=session,
                                            name=paper_name,
                                            classroom=classroom,
                                            class_name=cn,
                                            section=sn,
                                            date=dt,
                                            subject=subj,
                                            total_marks=tm,
                                            teacher=paper_teacher,
                                            created_by=request.user,
                                        )
                                        papers_created += 1
                            if sessions_created:
                                messages.success(
                                    request,
                                    f"Created {sessions_created} exam session(s) with {papers_created} subject paper(s).",
                                )
                            if skipped:
                                messages.warning(request, "Skipped: " + "; ".join(skipped[:25]))
                                if len(skipped) > 25:
                                    messages.warning(request, f"…and {len(skipped) - 25} more.")
                            if sessions_created or skipped:
                                return redirect("core:school_exams_list")
        else:
            single_form = SchoolExamSingleForm(school, request.POST)
            scheduler_form = SchoolExamSchedulerForm(school)
            if single_form.is_valid():
                cn = single_form.cleaned_data["class_name"]
                sn = single_form.cleaned_data["section"]
                subj = single_form.cleaned_data["subject"]
                dt = single_form.cleaned_data["date"]
                tm = single_form.cleaned_data["total_marks"]
                if _exam_duplicate(cn, sn, dt, subj):
                    messages.error(
                        request,
                        "An exam already exists for this class, section, date, and subject.",
                    )
                else:
                    classroom_obj = (
                        ClassRoom.objects.filter(name__iexact=cn.strip())
                        .select_related("academic_year")
                        .order_by("-academic_year__start_date", "id")
                        .first()
                    )
                    session_name = single_form.cleaned_data["name"].strip()[:100]
                    with transaction.atomic():
                        session = ExamSession.objects.create(
                            name=session_name,
                            class_name=cn,
                            section=sn,
                            classroom=classroom_obj,
                            created_by=request.user,
                        )
                        chosen = _exam_teacher_for_school(
                            school, single_form.cleaned_data.get("teacher")
                        )
                        paper_teacher = chosen or _default_teacher_for_class_section_subject(
                            school, classroom_obj, cn, sn, subj
                        )
                        Exam.objects.create(
                            session=session,
                            name=(subj.name[:100] if subj else session_name),
                            classroom=classroom_obj,
                            class_name=cn,
                            section=sn,
                            date=dt,
                            subject=subj,
                            total_marks=tm,
                            teacher=paper_teacher,
                            created_by=request.user,
                        )
                    messages.success(
                        request,
                        "Exam session created with one subject paper. Add more papers from Create exam (scheduler) or edit workflow.",
                    )
                    return redirect("core:school_exam_session_detail", session_id=session.pk)

    class_sections = {}
    for c in ClassRoom.objects.prefetch_related("sections").order_by("name"):
        class_sections[c.name] = [s.name for s in c.sections.order_by("name")]

    scheduler_class_sections = []
    for c in ClassRoom.objects.prefetch_related("sections").order_by(
        "academic_year__start_date", "name"
    ):
        scheduler_class_sections.append(
            {
                "id": c.id,
                "name": c.name,
                "sections": [{"id": s.id, "name": s.name} for s in c.sections.order_by("name")],
            }
        )

    existing = list(
        Exam.objects.filter(subject__isnull=False)
        .values("id", "name", "date", "class_name", "section", "subject_id")
        .order_by("-date")[:500]
    )

    scheduler_teachers = list(
        Teacher.objects.filter(user__school=school)
        .select_related("user")
        .order_by("user__first_name", "user__last_name", "id")
    )

    return render(
        request,
        "core/school/exam_create.html",
        {
            "single_form": single_form,
            "scheduler_form": scheduler_form,
            "scheduler_teachers": scheduler_teachers,
            "all_subjects": Subject.objects.order_by("name"),
            "all_classrooms": ClassRoom.objects.select_related("academic_year").order_by(
                "academic_year__start_date", "name"
            ),
            "class_sections_json": json.dumps(class_sections),
            "scheduler_class_sections_json": json.dumps(scheduler_class_sections, default=str),
            "existing_exams_json": json.dumps(
                [{"d": str(x["date"]), "c": x["class_name"], "s": x["section"], "sub": x["subject_id"]} for x in existing],
                default=str,
            ),
        },
    )


@login_required
@feature_required("exams")
def exam_events_json(request):
    """FullCalendar JSON feed — scoped by role; supports ?classroom=&section=&subject=&teacher= (admin teacher filter)."""
    events = [_exam_to_fc_event(e) for e in _exam_events_queryset(request)]
    return JsonResponse(events, safe=False)


@admin_required
@feature_required("exams")
@require_POST
def exam_patch_date_json(request):
    """Drag-and-drop: update exam date (admin only). Enforces class/section/day and teacher/day conflicts."""
    if not request.user.school:
        return JsonResponse({"error": "Forbidden"}, status=403)
    try:
        payload = json.loads(request.body.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    exam_id = payload.get("id")
    if exam_id is None or str(exam_id).strip() == "":
        return JsonResponse({"error": "Missing exam id"}, status=400)
    try:
        eid = int(exam_id)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid exam id"}, status=400)

    try:
        new_date = _parse_exam_calendar_date(payload.get("date"))
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid date"}, status=400)

    exam = _exam_read_qs().filter(pk=eid).first()
    if not exam:
        return JsonResponse({"error": "Exam not found"}, status=404)

    if _exam_class_section_date_conflict(exam.class_name, exam.section, new_date, exclude_pk=exam.pk):
        return JsonResponse(
            {"error": "This class and section already have another exam on that date."},
            status=409,
        )
    if _exam_teacher_date_conflict(exam.teacher_id, new_date, exclude_pk=exam.pk):
        return JsonResponse(
            {"error": "This teacher already has another exam on that date."},
            status=409,
        )

    exam.date = new_date
    exam.save(update_fields=["date"])
    return JsonResponse({"status": "ok", "id": exam.pk, "date": exam.date.isoformat()})


@login_required
@feature_required("exams")
def school_exam_calendar(request):
    """FullCalendar view: admin can drag-drop; teachers and students read-only."""
    school = getattr(request.user, "school", None)
    role = getattr(request.user, "role", None)
    can_edit = bool(school and role == "ADMIN")

    classrooms = ClassRoom.objects.none()
    sections = Section.objects.none()
    teachers = Teacher.objects.none()
    subjects = Subject.objects.order_by("name")
    has_filters = role in ("ADMIN", "TEACHER")

    if school and role == "ADMIN":
        classrooms = ClassRoom.objects.select_related("academic_year").order_by("academic_year__start_date", "name")
        sections = Section.objects.order_by("name")
        teachers = (
            Teacher.objects.filter(user__school=school)
            .select_related("user")
            .order_by("user__first_name", "user__last_name")
        )
    elif role == "TEACHER":
        teacher = getattr(request.user, "teacher_profile", None)
        if teacher:
            pairs = (
                ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
                .values_list("class_obj_id", "section_id")
                .distinct()
            )
            cids = {p[0] for p in pairs if p[0]}
            sids = {p[1] for p in pairs if p[1]}
            classrooms = ClassRoom.objects.filter(id__in=cids).order_by("name")
            sections = Section.objects.filter(id__in=sids).order_by("name")

    return render(
        request,
        "core/school/exam_calendar.html",
        {
            "can_edit": can_edit,
            "events_url": reverse("core:exam_events_json"),
            "patch_url": reverse("core:exam_patch_date_json"),
            "classrooms": classrooms,
            "sections": sections,
            "teachers": teachers,
            "subjects": subjects,
            "has_filters": has_filters,
        },
    )


@admin_required
@feature_required("exams")
def school_exam_edit(request, exam_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    # Full row: edit form includes start_time/end_time (requires DB columns from 0017).
    exam = get_object_or_404(Exam, pk=exam_id)
    from .forms import SchoolExamEditForm

    form = SchoolExamEditForm(school, request.POST or None, instance=exam)
    if request.method == "POST" and form.is_valid():
        cn = form.cleaned_data["class_name"]
        sn = form.cleaned_data["section"]
        dt = form.cleaned_data["date"]
        subj = form.cleaned_data.get("subject")
        teacher_obj = form.cleaned_data.get("teacher")

        if _exam_class_section_date_conflict(cn, sn, dt, exclude_pk=exam.pk):
            messages.error(request, "This class and section already have another exam on that date.")
        elif _exam_teacher_date_conflict(teacher_obj.pk if teacher_obj else None, dt, exclude_pk=exam.pk):
            messages.error(request, "This teacher already has another exam on that date.")
        elif _exam_duplicate(cn, sn, dt, subj, exclude_pk=exam.pk):
            messages.error(request, "An exam already exists for this class, section, date, and subject.")
        else:
            form.save()
            messages.success(request, "Exam updated.")
            return redirect("core:school_exam_calendar")

    return render(
        request,
        "core/school/exam_edit.html",
        {"form": form, "exam": exam, "title": "Edit exam"},
    )


@teacher_required
def teacher_exam_create(request):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")
    if not has_feature_access(school, "exams", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    teacher = getattr(request.user, "teacher_profile", None)
    from .utils import teacher_class_section_pairs_display

    allowed_pairs = teacher_class_section_pairs_display(teacher) if teacher else []
    if not allowed_pairs:
        messages.warning(
            request,
            "You have no class/section assignments. Contact admin to assign you to subjects before creating exams.",
        )
        return redirect("core:teacher_exams")
    from .forms import TeacherExamSessionPaperForm

    if request.method == "POST":
        form = TeacherExamSessionPaperForm(
            request.POST, allowed_pairs=allowed_pairs, teacher=teacher
        )
        if form.is_valid():
            cn = form.cleaned_data["class_name"].strip()
            sn = form.cleaned_data["section"].strip()
            subj = form.cleaned_data["subject"]
            dt = form.cleaned_data["date"]
            tm = form.cleaned_data["total_marks"]
            session_name = form.cleaned_data["session_name"].strip()[:100]
            if _exam_duplicate(cn, sn, dt, subj):
                messages.error(
                    request,
                    "A paper already exists for this class, section, date, and subject.",
                )
            elif _exam_class_section_date_conflict(cn, sn, dt):
                messages.error(
                    request,
                    "This class and section already have another exam on that date.",
                )
            elif _exam_teacher_date_conflict(teacher.id if teacher else None, dt):
                messages.error(request, "You already have another exam on that date.")
            else:
                classroom_obj = (
                    ClassRoom.objects.filter(name__iexact=cn)
                    .select_related("academic_year")
                    .order_by("-academic_year__start_date", "id")
                    .first()
                )
                with transaction.atomic():
                    session = ExamSession.objects.create(
                        name=session_name,
                        class_name=cn,
                        section=sn,
                        classroom=classroom_obj,
                        created_by=request.user,
                    )
                    paper = Exam.objects.create(
                        session=session,
                        name=subj.name[:100],
                        classroom=classroom_obj,
                        class_name=cn,
                        section=sn,
                        date=dt,
                        subject=subj,
                        total_marks=tm,
                        teacher=teacher,
                        created_by=request.user,
                    )
                messages.success(
                    request,
                    "Exam session and subject paper created. Use the same session name to add more subjects (admin scheduler) or create another session.",
                )
                return redirect("core:teacher_exam_session_detail", session_id=session.pk)
    else:
        form = TeacherExamSessionPaperForm(allowed_pairs=allowed_pairs, teacher=teacher)
    return render(request, "core/teacher/exam_create.html", {"form": form})


@teacher_required
def teacher_exam_summary(request, exam_id):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:teacher_dashboard")
    if not has_feature_access(school, "exams", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    exam = get_object_or_404(_exam_read_qs(), pk=exam_id)
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


def _exam_enter_marks_view(request, exam_id, *, acting_as_admin):
    """Shared marks entry for teachers (subject-scoped, may lock after save) and school admins (full access)."""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard" if acting_as_admin else "core:teacher_dashboard")
    if not has_feature_access(school, "exams", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    exam = get_object_or_404(_exam_read_qs(), pk=exam_id)
    teacher = getattr(request.user, "teacher_profile", None) if not acting_as_admin else None

    if acting_as_admin:
        subjects = (
            Subject.objects.filter(id=exam.subject_id).order_by("name")
            if exam.subject_id
            else Subject.objects.none()
        )
        subject_id_set = set()
    else:
        if not _teacher_exam_access(exam, school, teacher):
            raise PermissionDenied
        subject_ids = list(
            ClassSectionSubjectTeacher.objects.filter(
                teacher=teacher,
                class_obj__name__iexact=exam.class_name,
                section__name__iexact=exam.section,
            ).values_list("subject_id", flat=True).distinct()
        )
        subject_id_set = set(subject_ids)
        if exam.subject_id:
            if exam.subject_id in subject_id_set or (exam.teacher_id and exam.teacher_id == teacher.id):
                subjects = Subject.objects.filter(id=exam.subject_id).order_by("name")
            else:
                subjects = Subject.objects.none()
        else:
            subjects = Subject.objects.filter(id__in=subject_ids).order_by("name")

    enter_marks_url = reverse(
        "core:school_exam_paper_enter_marks" if acting_as_admin else "core:teacher_exam_enter_marks",
        args=[exam.id],
    )
    subject_id = request.GET.get("subject") or request.POST.get("subject")
    if request.method == "GET" and not subject_id and subjects.count() == 1:
        return redirect(f"{enter_marks_url}?subject={subjects.first().id}")

    students = list(
        Student.objects.filter(
            classroom__name__iexact=exam.class_name,
            section__name__iexact=exam.section,
            user__school=school,
        )
        .select_related("user")
        .order_by("roll_number")
    )
    subject = None
    if subject_id:
        subject = subjects.filter(id=subject_id).first()

    if request.method == "POST" and subject:
        if not acting_as_admin:
            exam.refresh_from_db(fields=["marks_teacher_edit_locked"])
            if exam.marks_teacher_edit_locked:
                messages.error(
                    request,
                    "Marks are locked. Ask your school admin to allow re-editing before you can save changes again.",
                )
                return redirect(f"{enter_marks_url}?subject={subject.id}")
        if acting_as_admin:
            if exam.subject_id and subject.id != exam.subject_id:
                raise PermissionDenied
        else:
            if exam.subject_id:
                if subject.id != exam.subject_id:
                    raise PermissionDenied
                if subject.id not in subject_id_set and exam.teacher_id != teacher.id:
                    raise PermissionDenied
            elif subject.id not in subject_id_set:
                raise PermissionDenied
        with transaction.atomic():
            existing = {
                (m.student_id, m.subject_id): m
                for m in Marks.objects.filter(exam=exam, subject=subject)
            }
            to_create = []
            to_update = []
            default_tm = exam.total_marks if getattr(exam, "total_marks", None) else 100
            for s in students:
                try:
                    obtained = int(request.POST.get(f"obtained_{s.id}", 0) or 0)
                    total = int(request.POST.get(f"total_{s.id}", default_tm) or default_tm)
                except (ValueError, TypeError):
                    obtained = 0
                    total = default_tm
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
        if not acting_as_admin:
            Exam.objects.filter(pk=exam.pk).update(marks_teacher_edit_locked=True)
            messages.success(request, "Marks saved. Further edits require school admin approval (marks are now locked).")
            return redirect("core:teacher_exam_summary", exam_id=exam.id)
        messages.success(request, "Marks saved.")
        if exam.session_id:
            return redirect("core:school_exam_session_detail", session_id=exam.session_id)
        return redirect("core:school_exams_list")

    existing_marks = {}
    if subject:
        for m in Marks.objects.filter(exam=exam, subject=subject):
            existing_marks[m.student_id] = {"obtained": m.marks_obtained, "total": m.total_marks}

    default_total = exam.total_marks if getattr(exam, "total_marks", None) else 100
    students_with_marks = []
    for s in students:
        em = existing_marks.get(s.id, {"obtained": 0, "total": default_total})
        students_with_marks.append({
            "student": s,
            "obtained": em["obtained"],
            "total": em["total"],
        })

    exam.refresh_from_db(fields=["marks_teacher_edit_locked"])
    marks_readonly = not acting_as_admin and bool(exam.marks_teacher_edit_locked)
    back_url = (
        reverse("core:school_exam_session_detail", args=[exam.session_id])
        if acting_as_admin and exam.session_id
        else reverse("core:school_exams_list")
        if acting_as_admin
        else reverse("core:teacher_exam_summary", args=[exam.id])
    )

    return render(
        request,
        "core/teacher/exam_enter_marks.html",
        {
            "exam": exam,
            "subjects": subjects,
            "subject": subject,
            "students_with_marks": students_with_marks,
            "acting_as_admin": acting_as_admin,
            "enter_marks_url": enter_marks_url,
            "marks_readonly": marks_readonly,
            "back_url": back_url,
        },
    )


@teacher_required
def teacher_exam_enter_marks(request, exam_id):
    return _exam_enter_marks_view(request, exam_id, acting_as_admin=False)


@admin_required
@feature_required("exams")
def school_exam_paper_enter_marks(request, exam_id):
    return _exam_enter_marks_view(request, exam_id, acting_as_admin=True)


@admin_required
@feature_required("exams")
@require_POST
def school_exam_paper_set_marks_lock(request, exam_id):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    exam = get_object_or_404(Exam.objects.only("id", "session_id"), pk=exam_id)
    raw = (request.POST.get("marks_teacher_edit_locked") or "").strip().lower()
    if raw == "1":
        locked = True
    elif raw == "0":
        locked = False
    else:
        messages.error(request, "Invalid lock action.")
        if exam.session_id:
            return redirect("core:school_exam_session_detail", session_id=exam.session_id)
        return redirect("core:school_exams_list")
    Exam.objects.filter(pk=exam.pk).update(marks_teacher_edit_locked=locked)
    messages.success(
        request,
        "Teachers cannot edit marks for this paper until you allow re-editing."
        if locked
        else "Teachers can save mark changes again for this paper.",
    )
    if exam.session_id:
        return redirect("core:school_exam_session_detail", session_id=exam.session_id)
    return redirect("core:school_exams_list")


@admin_required
@feature_required("exams")
@require_POST
def school_exam_session_set_all_marks_lock(request, session_id):
    """Lock or unlock teacher mark editing for every paper in this exam session."""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    session_obj = get_object_or_404(ExamSession.objects.only("id"), pk=session_id)
    raw = (request.POST.get("marks_teacher_edit_locked") or "").strip().lower()
    if raw == "1":
        locked = True
    elif raw == "0":
        locked = False
    else:
        messages.error(request, "Invalid lock action.")
        return redirect("core:school_exam_session_detail", session_id=session_id)
    n = Exam.objects.filter(session=session_obj).update(marks_teacher_edit_locked=locked)
    if locked:
        messages.success(
            request,
            f"Locked mark editing for teachers on all {n} subject paper(s) in this session.",
        )
    else:
        messages.success(
            request,
            f"Teachers can save mark changes again on all {n} subject paper(s) in this session.",
        )
    return redirect("core:school_exam_session_detail", session_id=session_id)


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
    if not has_feature_access(school, "attendance", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")

    today = date.today()
    teacher = getattr(request.user, "teacher_profile", None)
    from .utils import teacher_class_section_pairs_display, teacher_allowed_class_section_pairs_lower

    allowed_pairs_raw = teacher_class_section_pairs_display(teacher) if teacher else []
    allowed_pairs_lower = teacher_allowed_class_section_pairs_lower(teacher) if teacher else set()

    # Populate dropdowns from teacher mappings only (value, label) tuples for the template.
    class_choices = [(n, n) for n in sorted({c for c, _ in allowed_pairs_raw if c})]
    section_choices = [(n, n) for n in sorted({s for _, s in allowed_pairs_raw if s})]

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
    if not has_feature_access(school, "attendance", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")

    teacher = getattr(request.user, "teacher_profile", None)
    from .utils import teacher_class_section_pairs_display, teacher_allowed_class_section_pairs_lower

    allowed_pairs_raw = teacher_class_section_pairs_display(teacher) if teacher else []
    allowed_pairs_lower = teacher_allowed_class_section_pairs_lower(teacher) if teacher else set()

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
    from apps.accounts.models import User

    school = getattr(request.user, "school", None)
    if not school:
        return None
    if getattr(request.user, "role", None) == User.Roles.SUPERADMIN:
        return school
    if school.is_trial_expired():
        return None
    if not school.has_feature(feature):
        return None
    return school


def _fee_balance_remaining(fee):
    from decimal import Decimal

    paid = Payment.objects.filter(fee=fee).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    return fee.amount - paid


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
    """Generate fee dues from structure, and record student-wise payments (same page)."""
    from decimal import Decimal

    from .forms import PaymentForm

    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    students_qs = Student.objects.select_related("user", "classroom", "section").order_by(
        "user__last_name", "user__first_name", "user__username"
    )

    selected_student = None
    pending_fees = []
    record_form = None
    preselected_fee_id = None

    student_param = request.POST.get("record_student") or request.GET.get("student")
    if student_param:
        try:
            selected_student = students_qs.get(pk=int(student_param))
        except (ValueError, Student.DoesNotExist):
            selected_student = None
        if selected_student:
            pending_fees = list(
                Fee.objects.filter(
                    student=selected_student,
                    status__in=["PENDING", "PARTIAL"],
                )
                .select_related("fee_structure__fee_type")
                .order_by("due_date")
            )

    fee_qs = request.GET.get("fee") or request.POST.get("record_fee")
    if fee_qs:
        try:
            preselected_fee_id = int(fee_qs)
        except (TypeError, ValueError):
            preselected_fee_id = None

    if pending_fees:
        valid_fee_ids = {f.id for f in pending_fees}
        if preselected_fee_id not in valid_fee_ids:
            preselected_fee_id = pending_fees[0].id

    if request.method == "POST" and request.POST.get("record_payment"):
        fee_obj = None
        sid = request.POST.get("record_student")
        fid = request.POST.get("record_fee")
        if sid and fid:
            try:
                fee_obj = Fee.objects.select_related("student").get(
                    pk=int(fid),
                    student_id=int(sid),
                    status__in=["PENDING", "PARTIAL"],
                )
            except (ValueError, Fee.DoesNotExist):
                fee_obj = None
        if fee_obj:
            record_form = PaymentForm(request.POST, fee=fee_obj)
            if record_form.is_valid():
                with transaction.atomic():
                    p = record_form.save(commit=False)
                    p.fee = fee_obj
                    p.received_by = request.user
                    p.save()
                    paid = Payment.objects.filter(fee=fee_obj).aggregate(s=Sum("amount"))["s"] or Decimal("0")
                    if paid >= fee_obj.amount:
                        fee_obj.status = "PAID"
                    else:
                        fee_obj.status = "PARTIAL"
                    fee_obj.save(update_fields=["status"])
                messages.success(
                    request,
                    f"Recorded {p.amount} from {fee_obj.student.user.get_full_name() or fee_obj.student.user.username} "
                    f"({p.payment_method}).",
                )
                return redirect(f"{reverse('core:school_fee_add')}?student={fee_obj.student_id}")
        else:
            record_form = PaymentForm(request.POST, fee=None)
            messages.error(request, "Select a student and a pending fee due, then try again.")
    elif request.method == "GET" and selected_student and pending_fees:
        target_fee = None
        if preselected_fee_id:
            for pf in pending_fees:
                if pf.id == preselected_fee_id:
                    target_fee = pf
                    break
        if target_fee is None:
            target_fee = pending_fees[0]
        remaining = _fee_balance_remaining(target_fee)
        record_form = PaymentForm(
            fee=target_fee,
            initial={"payment_date": date.today(), "amount": remaining},
        )

    if request.method == "POST" and not request.POST.get("record_payment"):
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
                messages.success(request, f"Generated {created} new fee due(s).")
            except (ValueError, FeeStructure.DoesNotExist):
                messages.error(request, "Could not generate fees. Check the form and try again.")
        return redirect("core:school_fee_collection")

    structures = FeeStructure.objects.all().select_related("fee_type", "classroom")
    classrooms = ClassRoom.objects.all()
    pending_rows = []
    for f in pending_fees:
        pending_rows.append({"fee": f, "balance": _fee_balance_remaining(f)})

    return render(
        request,
        "core/fees/fee_add.html",
        {
            "structures": structures,
            "classrooms": classrooms,
            "students": students_qs,
            "selected_student": selected_student,
            "pending_rows": pending_rows,
            "record_form": record_form,
            "preselected_fee_id": preselected_fee_id,
        },
    )


@admin_required
def school_fee_collection(request):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    dues = Fee.objects.filter(status__in=["PENDING", "PARTIAL"]).select_related(
        "student__user", "fee_structure__fee_type"
    ).order_by("due_date")
    students = Student.objects.select_related("user").order_by("user__last_name", "user__first_name")
    student_filter = (request.GET.get("student") or "").strip()
    filtered_student = None
    if student_filter:
        try:
            fid = int(student_filter)
            filtered_student = Student.objects.select_related("user").filter(pk=fid).first()
            dues = dues.filter(student_id=fid)
        except ValueError:
            pass
    return render(
        request,
        "core/fees/fee_collection.html",
        {
            "dues": dues,
            "students": students,
            "student_filter": student_filter,
            "filtered_student": filtered_student,
        },
    )


@admin_required
def school_fee_collect(request, fee_id):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    fee = get_object_or_404(Fee, id=fee_id)
    from .forms import PaymentForm

    balance_due = _fee_balance_remaining(fee)
    if request.method == "POST":
        form = PaymentForm(request.POST, fee=fee)
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
            messages.success(request, "Payment recorded.")
            fee_student_id = fee.student_id
            base = reverse("core:school_fee_collection")
            if fee_student_id:
                return redirect(f"{base}?{urlencode({'student': fee_student_id})}")
            return redirect("core:school_fee_collection")
    else:
        form = PaymentForm(
            fee=fee,
            initial={"payment_date": date.today(), "amount": balance_due},
        )
    return render(
        request,
        "core/fees/collect.html",
        {"form": form, "fee": fee, "balance_due": balance_due},
    )


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
    hw_legacy_ids = set()
    for c in children:
        if c.classroom_id and c.section_id:
            subj_ids = ClassSectionSubjectTeacher.objects.filter(
                class_obj_id=c.classroom_id,
                section_id=c.section_id,
            ).values_list("subject_id", flat=True)
            hw_legacy_ids.update(
                Homework.objects.filter(subject_id__in=subj_ids).values_list("id", flat=True)
            )
            hw_legacy_ids.update(
                Homework.objects.filter(classes=c.classroom, sections=c.section).values_list("id", flat=True)
            )
    hw = list(Homework.objects.filter(id__in=hw_legacy_ids).prefetch_related("classes", "sections").select_related("subject").order_by("-due_date")[:20])
    return render(request, "core/parent/announcements.html", {
        "announcements": hw,
        "title": "Homework / Announcements",
    })


# ======================
# Student profile PDF (full profile summary)
# ======================


@admin_required
def school_student_profile_pdf(request, student_id):
    """Download student profile as PDF (xhtml2pdf). Filename: student_<id>.pdf."""
    school = request.user.school
    if not school:
        raise PermissionDenied
    student = get_object_or_404(
        Student.objects.select_related("user", "classroom", "section", "academic_year"),
        id=student_id,
    )
    extra = student.extra_data or {}
    basic = extra.get("basic") or {}
    academic = extra.get("academic") or {}
    parents = extra.get("parents") or {}
    contact = extra.get("contact") or {}
    status_block = extra.get("status") or {}
    addr_lines = (student.address or "").split("\n") if student.address else []
    address_line1 = addr_lines[0] if addr_lines else ""
    address_line2 = addr_lines[1] if len(addr_lines) > 1 else ""
    ctx = {
        "student": student,
        "school": school,
        "basic": basic,
        "academic": academic,
        "parents": parents,
        "contact": contact,
        "status_block": status_block,
        "address_line1": address_line1,
        "address_line2": address_line2,
        "generated_on": timezone.now(),
    }
    ctx.update(_student_record_letterhead_context(request, school, student))
    pdf_bytes = render_pdf_bytes("core/school/student_profile_pdf.html", ctx)
    if not pdf_bytes:
        raise Http404("PDF could not be generated. Ensure xhtml2pdf is installed.")
    return pdf_response(pdf_bytes, f"student_{student_id}.pdf")


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
    """Public admission form. School must have online_admission (Enterprise tier)."""
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
    """Backward-compatible redirect to reports toppers view."""
    return redirect("reports:toppers")


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

