from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model, login as auth_login
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.core.files.storage import default_storage
from django.http import Http404, HttpResponse, JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from datetime import date, timedelta
from decimal import Decimal
from calendar import monthrange
from io import BytesIO
import csv
import json
import uuid
from urllib.parse import urlencode
import logging
from functools import reduce
from operator import or_ as _or_
from django.utils import timezone

logger = logging.getLogger(__name__)

from django.db import connection, transaction
from django.db.models import Case, Count, F, IntegerField, Max, Min, OuterRef, Prefetch, Q, Subquery, Sum, Value, When
from django.db.models.functions import Lower
from django.core.paginator import Paginator
from django.db.utils import DatabaseError, InternalError, IntegrityError, OperationalError, ProgrammingError
from apps.core.tenant_scope import ensure_tenant_for_request
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
from apps.school_data.exam_session_compat import examsession_create_compat
from apps.school_data.homework_schema_repair import (
    ensure_homework_audit_columns_if_missing,
    ensure_homework_enterprise_columns_if_missing,
)
from apps.school_data.classroom_ordering import (
    ORDER_AY_PK_GRADE_NAME,
    ORDER_AY_START_GRADE_NAME,
    ORDER_GRADE_NAME,
)
from apps.school_data.models import (
    Student,
    Teacher,
    Attendance,
    Homework,
    HomeworkSubmission,
    HomeworkSubmissionAttempt,
    Marks,
    Subject,
    ClassRoom,
    Exam,
    ExamSession,
    Section,
    ClassSectionSubjectTeacher,
    ClassSectionTeacher,
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
    HolidayCalendar,
    HolidayEvent,
    WorkingSundayOverride,
    MasterDataOption,
    StudentResource,
    StudentMessage,
    StudentAnnouncement,
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
    ExamSessionPaperFormSet,
    HolidayEventForm,
    WorkingSundayOverrideForm,
    SaaSPlatformPaymentForm,
    SchoolEnrollmentSignupForm,
    SchoolExamSessionEditForm,
    SuperAdminEnrollmentDeclineForm,
    SuperAdminEnrollmentProvisionForm,
)
from .models import ContactEnquiry, SchoolEnrollmentRequest
from apps.accounts.decorators import (
    admin_required,
    superadmin_required,
    student_required,
    teacher_required,
    teacher_or_admin_required,
    parent_required,
    feature_required,
)
from apps.core.pdf_utils import pdf_response, render_pdf_bytes
from apps.core.student_attendance_services import get_student_attendance_summary
from apps.notifications.models import Message as InternalMessage


def _distinct_day_streak(dates_desc: list[date]) -> int:
    """
    Return consecutive-day streak length from a descending list of dates (deduped not required).
    """
    if not dates_desc:
        return 0
    # Dedup while keeping desc order
    seen = set()
    days = []
    for d in dates_desc:
        if d in seen:
            continue
        seen.add(d)
        days.append(d)
    if not days:
        return 0
    streak = 1
    for i in range(1, len(days)):
        if (days[i - 1] - days[i]).days == 1:
            streak += 1
        else:
            break
    return streak


def _compute_student_achievement_badges(*, student, attendance_pct: float, overall_pct: float) -> list[dict]:
    """
    Compute and award student achievement badges (safe, idempotent).
    Returns template-friendly list of dicts.
    """
    try:
        # Homework streak: consecutive submission days (based on attempt log)
        hw_days = list(
            HomeworkSubmissionAttempt.objects.filter(student=student)
            .exclude(submitted_at__isnull=True)
            .order_by("-submitted_at")
            .values_list("submitted_at", flat=True)[:40]
        )
        hw_day_dates = [dt.date() for dt in hw_days if dt]
        homework_streak_days = _distinct_day_streak(hw_day_dates)
    except Exception:
        homework_streak_days = 0

    earned = []
    try:
        perfect_attendance, _ = Badge.objects.get_or_create(
            name="Perfect Attendance 🏆",
            defaults={"description": "Awarded for 100% attendance in the academic year.", "icon": "bi bi-award-fill"},
        )
        top_scorer, _ = Badge.objects.get_or_create(
            name="Top Scorer 🎖️",
            defaults={"description": "Awarded for 90%+ overall marks.", "icon": "bi bi-trophy-fill"},
        )
        homework_streak, _ = Badge.objects.get_or_create(
            name="Homework Streak 🔥",
            defaults={"description": "Awarded for 5+ consecutive homework submission days.", "icon": "bi bi-fire"},
        )
    except (OperationalError, ProgrammingError):
        return []

    def _award(badge, *, tone: str, meta: str):
        try:
            StudentBadge.objects.get_or_create(student=student, badge=badge)
        except Exception:
            pass
        earned.append(
            {
                "name": badge.name,
                "icon": getattr(badge, "icon", "") or "bi bi-star-fill",
                "description": getattr(badge, "description", "") or "",
                "tone": tone,  # success | danger | primary | warning
                "meta": meta,
            }
        )

    if attendance_pct >= 100 and attendance_pct > 0:
        _award(perfect_attendance, tone="success", meta=f"{attendance_pct}% attendance")
    if overall_pct >= 90:
        _award(top_scorer, tone="primary", meta=f"{overall_pct}% overall")
    if homework_streak_days >= 5:
        _award(homework_streak, tone="warning", meta=f"{homework_streak_days} day streak")

    return earned

# ======================
# Public Pages
# ======================

def home(request):
    return render(request, "marketing/home.html")


def pricing(request):
    # Public page: always use public schema for plan marketing.
    try:
        from django.db import connection

        connection.set_schema_to_public()
    except Exception:
        pass

    comparison = None
    pricing_plans = []
    try:
        from apps.super_admin.models import Feature, Plan, PlanName

        plans_by_key = {p.name: p for p in Plan.objects.filter(is_active=True).prefetch_related("features")}
        ordered_plans = [plans_by_key.get(PlanName.BASIC), plans_by_key.get(PlanName.PRO), plans_by_key.get(PlanName.PREMIUM)]
        ordered_plans = [p for p in ordered_plans if p is not None]

        pricing_plans = [
            {
                "id": p.id,
                "key": p.name,
                "label": p.get_name_display(),
                "price": p.price,
                "list_price": getattr(p, "list_price", None) or 0,
                "recommended": p.name == PlanName.PREMIUM,
            }
            for p in ordered_plans
        ]

        plan_cols = [{"id": p.id, "key": p.name, "label": p.get_name_display()} for p in ordered_plans]
        plan_codes = {p.id: set(p.features.values_list("code", flat=True)) for p in ordered_plans}

        features = list(Feature.objects.all().order_by("category", "name"))
        groups: list[dict] = []
        current_cat = None
        current_rows = []

        def _flush():
            nonlocal current_cat, current_rows
            if current_cat is None:
                return
            groups.append({"category": current_cat, "rows": current_rows})
            current_cat = None
            current_rows = []

        for f in features:
            cat = (getattr(f, "category", "") or "other").strip().lower()
            if current_cat is None:
                current_cat = cat
            if cat != current_cat:
                _flush()
                current_cat = cat
            states = []
            for col in plan_cols:
                states.append(
                    {
                        "key": col["key"],
                        "enabled": bool(f.code in plan_codes.get(col["id"], set())),
                    }
                )
            current_rows.append({"feature": f, "states": states})
        _flush()

        comparison = {"plan_cols": plan_cols, "groups": groups}
    except Exception:
        comparison = None

    return render(request, "marketing/pricing.html", {"comparison": comparison, "pricing_plans": pricing_plans})


def about(request):
    return render(request, "marketing/about.html")


@transaction.non_atomic_requests
def school_enrollment_signup(request):
    """
    Public self-service signup: creates School (tenant + migrations), admin User, seeds tenant,
    then redirects to this page (GET) with a success screen so the browser gets a light response
    after the heavy provisioning work (avoids connection resets on long POSTs).
    """
    from .enrollment_storage import ensure_school_enrollment_storage
    from .self_service_enrollment import SelfServiceEnrollmentError, provision_school_and_admin_user

    success = request.GET.get("success") == "1"
    enrollment_success = None
    enrollment_error = None
    if request.GET.get("completed") == "1":
        enrollment_success = request.session.pop("enrollment_success", None)
        if not enrollment_success:
            return redirect(reverse("core:school_enroll"))
    if request.GET.get("failed") == "1":
        enrollment_error = request.session.pop("enrollment_error", None)
        if not enrollment_error:
            return redirect(reverse("core:school_enroll"))

    enroll_generic_error = (
        "We couldn't complete your enrollment right now. Please try again in a few moments, "
        "or contact support if the problem continues."
    )
    if request.method == "POST":
        form = SchoolEnrollmentSignupForm(request.POST, request.FILES)
        if form.is_valid():
            ensure_school_enrollment_storage()
            try:
                school, user = provision_school_and_admin_user(form.cleaned_data)
            except SelfServiceEnrollmentError as exc:
                form.add_error(None, str(exc))
            except Exception:
                logger.exception("Self-service enrollment failed")
                request.session["enrollment_error"] = enroll_generic_error
                return redirect(f"{reverse('core:school_enroll')}?failed=1")
            else:
                request.session["enrollment_success"] = {
                    "school_name": school.name,
                    "school_code": school.code,
                    "username": user.username,
                    "schema_name": school.schema_name,
                }
                # After provisioning, send the user to login rather than keeping them on /enroll/.
                # This avoids being "stuck" on the enrollment page and aligns with production UX.
                try:
                    messages.success(
                        request,
                        f"School created successfully. Login with username “{user.username}”.",
                    )
                except Exception:
                    pass
                return redirect(reverse("accounts:login"))
    else:
        form = SchoolEnrollmentSignupForm()

    enroll_plans: list[dict] = []
    try:
        from django.db import connection

        connection.set_schema_to_public()
        from apps.super_admin.models import Plan, PlanName

        plans_by_key = {p.name: p for p in Plan.objects.filter(is_active=True)}
        ordered = [
            plans_by_key.get(PlanName.BASIC),
            plans_by_key.get(PlanName.PRO),
            plans_by_key.get(PlanName.PREMIUM),
        ]
        enroll_plans = [
            {
                "key": p.name,
                "label": p.get_name_display(),
                "price": p.price,
                "list_price": getattr(p, "list_price", None) or 0,
                "recommended": p.name == PlanName.PREMIUM,
            }
            for p in ordered
            if p is not None
        ]
    except Exception:
        enroll_plans = []

    if not enroll_plans:
        from decimal import Decimal

        enroll_plans = [
            {
                "key": "basic",
                "label": "Basic",
                "price": Decimal("49"),
                "list_price": Decimal("59"),
                "recommended": False,
            },
            {
                "key": "pro",
                "label": "Pro",
                "price": Decimal("79"),
                "list_price": Decimal("89"),
                "recommended": False,
            },
            {
                "key": "premium",
                "label": "Premium",
                "price": Decimal("89"),
                "list_price": Decimal("99"),
                "recommended": True,
            },
        ]

    return render(
        request,
        "marketing/enroll.html",
        {
            "form": form,
            "success": success,
            "enrollment_success": enrollment_success,
            "enrollment_error": enrollment_error,
            "enroll_plans": enroll_plans,
        },
    )


def contact(request):
    success = request.GET.get("success") == "1"
    # Company contact details are stored in DB on the public tenant record.
    # We reuse the public tenant School row (schema_name="public") as the platform/company profile.
    company = None
    try:
        from apps.customers.models import School

        company = School.objects.filter(schema_name="public").only("name", "phone", "contact_email", "address").first()
    except Exception:
        company = None

    prefill = (request.GET.get("prefill") or "").strip().lower()
    initial = {}
    if prefill == "trial_expired":
        initial["message"] = "My trial has expired, I want to upgrade my plan."

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
        form = ContactEnquiryForm(initial=initial)
    return render(
        request,
        "marketing/contact.html",
        {"form": form, "success": success, "company": company, "prefill": prefill},
    )


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
# School Admin: Master Data APIs (tenant-scoped)
# ======================

_VALID_MASTER_KEYS = frozenset({k for k, _ in MasterDataOption.Key.choices})


def _parse_master_json(request):
    raw_body = ""
    try:
        raw_body = (request.body or b"").decode("utf-8", errors="ignore")
    except Exception:
        raw_body = ""
    try:
        payload = json.loads(raw_body or "{}")
    except Exception:
        payload = dict(request.POST or {})
    return raw_body, payload


def _master_data_update_from_payload(request, key: str, option_id: int, payload: dict):
    """Apply update fields from payload to MasterDataOption (key + id)."""
    school = request.user.school
    if not school:
        return JsonResponse({"error": "Unauthorized."}, status=403)
    key = (key or "").strip()
    if key not in _VALID_MASTER_KEYS:
        return JsonResponse({"error": "Invalid master key."}, status=400)

    obj = MasterDataOption.objects.filter(pk=option_id, key=key).first()
    if not obj:
        return JsonResponse({"error": "Not found."}, status=404)

    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "Please enter a valid value."}, status=400)
        obj.name = name
    if "display_order" in payload:
        try:
            obj.display_order = int(payload.get("display_order") or 0)
        except Exception:
            obj.display_order = 0
    if "is_active" in payload:
        obj.is_active = bool(payload.get("is_active"))

    try:
        obj.save_with_audit(request.user)
    except IntegrityError:
        return JsonResponse({"error": "This option already exists."}, status=409)
    except Exception:
        logger.exception("master_data_update failed (key=%s, id=%s)", key, option_id)
        return JsonResponse({"error": "Could not save. Please try again."}, status=500)
    return JsonResponse(
        {
            "ok": True,
            "id": obj.id,
            "name": obj.name,
            "name_normalized": obj.name_normalized,
            "is_active": obj.is_active,
            "display_order": obj.display_order,
        }
    )


@admin_required
@require_GET
def master_data_list(request, key: str):
    """
    GET /api/master-data/<key>/list/ -> {"options": [{"id": 1, "name": "X"}]}

    Tenant-scoped by schema; only active options are returned.
    """
    school = request.user.school
    if not school:
        return JsonResponse({"options": []}, status=403)
    key = (key or "").strip()
    if key not in _VALID_MASTER_KEYS:
        return JsonResponse({"error": "Invalid master key."}, status=400)
    opts = (
        MasterDataOption.objects.filter(key=key, is_active=True)
        .order_by("display_order", "name")
        .values("id", "name")
    )
    return JsonResponse({"options": list(opts)})


@admin_required
@require_POST
def master_data_update(request, key: str, option_id: int):
    """POST /api/master-data/<key>/<id>/update/ (JSON) -> update name/order/is_active."""
    try:
        payload = json.loads((request.body or b"{}").decode("utf-8") or "{}")
    except Exception:
        payload = {}
    return _master_data_update_from_payload(request, (key or "").strip(), option_id, payload)


@admin_required
@require_POST
def master_data_delete(request, key: str, option_id: int):
    """POST /api/master-data/<key>/<id>/delete/ -> permanently remove the option row."""
    school = request.user.school
    if not school:
        return JsonResponse({"error": "Unauthorized."}, status=403)
    key = (key or "").strip()
    if key not in _VALID_MASTER_KEYS:
        return JsonResponse({"error": "Invalid master key."}, status=400)
    obj = MasterDataOption.objects.filter(pk=option_id, key=key).first()
    if not obj:
        return JsonResponse({"error": "Not found."}, status=404)
    try:
        obj.delete()
    except Exception:
        logger.exception("master_data_delete failed (key=%s, id=%s)", key, option_id)
        return JsonResponse({"error": "Could not delete. Please try again."}, status=500)
    return JsonResponse({"ok": True})


@admin_required
@require_POST
def master_data_reorder(request, key: str):
    """POST /api/master-data/<key>/reorder/ (JSON: {"ids":[1,2,3]}) -> set display_order."""
    school = request.user.school
    if not school:
        return JsonResponse({"error": "Unauthorized."}, status=403)
    key = (key or "").strip()
    if key not in _VALID_MASTER_KEYS:
        return JsonResponse({"error": "Invalid master key."}, status=400)
    try:
        payload = json.loads((request.body or b"{}").decode("utf-8") or "{}")
    except Exception:
        payload = {}
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return JsonResponse({"error": "Invalid ids."}, status=400)
    try:
        with transaction.atomic():
            for idx, oid in enumerate(ids):
                MasterDataOption.objects.filter(pk=int(oid), key=key).update(display_order=idx)
    except Exception:
        logger.exception("master_data_reorder failed (key=%s)", key)
        return JsonResponse({"error": "Could not reorder. Please try again."}, status=500)
    return JsonResponse({"ok": True})


def _master_data_create_core(request, key: str):
    """
    Shared create implementation. ``key`` must already be validated against ``_VALID_MASTER_KEYS``.
    """
    school = request.user.school
    if not school:
        return JsonResponse({"error": "Unauthorized."}, status=403)
    raw_body, payload = _parse_master_json(request)
    name = payload.get("name") or ""
    if isinstance(name, list):
        name = name[0] if name else ""
    name = (name or "").strip()
    if not name:
        return JsonResponse({"error": "Please enter a valid value."}, status=400)
    name_norm = name.strip().lower()
    if MasterDataOption.objects.filter(key=key, name_normalized=name_norm).exists():
        return JsonResponse({"error": "This option already exists."}, status=409)
    try:
        with transaction.atomic():
            next_order = (
                (MasterDataOption.objects.filter(key=key).aggregate(m=Max("display_order")).get("m") or -1) + 1
            )
            obj = MasterDataOption(key=key, name=name, display_order=next_order)
            obj.save_with_audit(request.user)
    except IntegrityError:
        return JsonResponse({"error": "This option already exists."}, status=409)
    except (ProgrammingError, InternalError, DatabaseError):
        error_id = uuid.uuid4().hex
        logger.exception(
            "master_data_create DB error [%s] (schema=%s, key=%s, name=%s)",
            error_id,
            getattr(connection, "schema_name", None),
            key,
            name,
        )
        return JsonResponse(
            {
                "error": "Database schema is not ready for this dropdown. Please run tenant migrations and try again.",
                "error_id": error_id,
            },
            status=500,
        )
    except Exception:
        error_id = uuid.uuid4().hex
        logger.exception(
            "master_data_create failed [%s] (schema=%s, key=%s, name=%s, body=%s)",
            error_id,
            getattr(connection, "schema_name", None),
            key,
            name,
            raw_body[:500],
        )
        from django.conf import settings

        msg = "Could not save. Please try again."
        if getattr(settings, "DEBUG", False):
            msg = f"{msg} (see server log, Error ID: {error_id})"
        return JsonResponse({"error": msg, "error_id": error_id}, status=500)
    return JsonResponse(
        {
            "id": obj.id,
            "name": obj.name,
            "name_normalized": obj.name_normalized,
            "display_order": obj.display_order,
            "is_active": obj.is_active,
        },
        status=201,
    )


@admin_required
@require_POST
def master_data_create(request, key: str):
    """
    POST /api/master-data/<key>/create/ (JSON: {"name": "X"}) ->
      201 {"id": 1, "name": "X", ...}
      409 {"error": "This option already exists."}
    """
    school = request.user.school
    if not school:
        return JsonResponse({"error": "Unauthorized."}, status=403)
    key = (key or "").strip()
    if key not in _VALID_MASTER_KEYS:
        return JsonResponse({"error": "Invalid master key."}, status=400)
    return _master_data_create_core(request, key)


@admin_required
@require_POST
def master_dropdown_add_option(request):
    """POST /api/master-dropdown/add-option/ JSON: {"key": "gender", "name": "..."}"""
    _, payload = _parse_master_json(request)
    key = (payload.get("key") or "").strip()
    if key not in _VALID_MASTER_KEYS:
        return JsonResponse({"error": "Invalid master key."}, status=400)
    return _master_data_create_core(request, key)


@admin_required
@require_POST
def master_dropdown_update(request):
    """POST /api/master-dropdown/update/ JSON: {"key","id","name","display_order","is_active"}"""
    try:
        payload = json.loads((request.body or b"{}").decode("utf-8") or "{}")
    except Exception:
        payload = {}
    key = (payload.get("key") or "").strip()
    if key not in _VALID_MASTER_KEYS:
        return JsonResponse({"error": "Invalid master key."}, status=400)
    try:
        option_id = int(payload.get("id"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid id."}, status=400)
    update_payload = {k: payload[k] for k in ("name", "display_order", "is_active") if k in payload}
    return _master_data_update_from_payload(request, key, option_id, update_payload)


@admin_required
@require_POST
def master_dropdown_save_order(request):
    """POST /api/master-dropdown/save-order/ JSON: {"key":"gender","items":[{"id":1,"order":0}, ...]}"""
    school = request.user.school
    if not school:
        return JsonResponse({"error": "Unauthorized."}, status=403)
    try:
        payload = json.loads((request.body or b"{}").decode("utf-8") or "{}")
    except Exception:
        payload = {}
    key = (payload.get("key") or "").strip()
    if key not in _VALID_MASTER_KEYS:
        return JsonResponse({"error": "Invalid master key."}, status=400)
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return JsonResponse({"error": "Invalid items."}, status=400)
    try:
        with transaction.atomic():
            for it in items:
                oid = int(it.get("id"))
                ord_val = int(it.get("order", 0))
                MasterDataOption.objects.filter(pk=oid, key=key).update(display_order=ord_val)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid items payload."}, status=400)
    except Exception:
        logger.exception("master_dropdown_save_order failed (key=%s)", key)
        return JsonResponse({"error": "Could not reorder. Please try again."}, status=500)
    return JsonResponse({"ok": True})


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
                    addr_parts = []
                    if (enrollment.address or "").strip():
                        addr_parts.append(enrollment.address.strip())
                    loc = ", ".join(
                        x
                        for x in [enrollment.city, enrollment.state, enrollment.pincode]
                        if (x or "").strip()
                    )
                    if loc:
                        addr_parts.append(loc)
                    meta_bits = []
                    if getattr(enrollment, "society_name", None) and (enrollment.society_name or "").strip():
                        meta_bits.append(f"Society: {enrollment.society_name.strip()}")
                    if (enrollment.institution_code or "").strip():
                        meta_bits.append(f"Code: {enrollment.institution_code.strip()}")
                    for label, val in (
                        ("Students", enrollment.student_count),
                        ("Teachers", enrollment.teacher_count),
                        ("Branches", enrollment.branch_count),
                    ):
                        if val is not None:
                            meta_bits.append(f"{label}: {val}")
                    if (enrollment.preferred_username or "").strip():
                        meta_bits.append(f"Preferred login: {enrollment.preferred_username.strip()}")
                    if (enrollment.intended_plan or "").strip():
                        meta_bits.append(f"Plan preference: {enrollment.intended_plan.strip()}")
                    notes_tail = enrollment.notes or ""
                    if meta_bits:
                        notes_tail = (notes_tail + "\n\n" if notes_tail else "") + " · ".join(meta_bits)
                    address_notes = "\n\n".join(p for p in [*addr_parts, notes_tail] if p).strip()
                    from django.core.exceptions import ValidationError as ProvValidationError

                    try:
                        school = provision_school_from_enrollment(
                            institution_name=enrollment.institution_name,
                            contact_email=enrollment.email,
                            phone=enrollment.phone or "",
                            address_notes=address_notes,
                            subscription_plan=sub,
                            saas_plan=saas,
                            school_code=(enrollment.institution_code or "").strip() or None,
                        )
                    except ProvValidationError as exc:
                        messages.error(
                            request,
                            exc.messages[0] if getattr(exc, "messages", None) else str(exc),
                        )
                        return redirect("core:superadmin_enrollment_detail", pk=enrollment.pk)
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
    from .platform_footprint import build_footprint_school_rows

    snap = build_super_admin_platform_snapshot()
    billing_summary = summarize_billing_rows(snap["billing_rows"])
    plans = Plan.sale_tiers().prefetch_related("features")
    pending_enrollments = SchoolEnrollmentRequest.objects.filter(
        status=SchoolEnrollmentRequest.Status.PENDING
    ).count()
    _t, _s, _c, footprint_school_rows = build_footprint_school_rows(q=None)
    return render(
        request,
        "core/dashboards/super_admin_dashboard.html",
        {
            "total_schools": snap["total_schools"],
            "total_teachers": snap["total_teachers"],
            "total_students": snap["total_students"],
            "total_classes": snap["total_classes"],
            "footprint_school_rows": footprint_school_rows,
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
def superadmin_schools_overview(request):
    """Card-style per-school overview (click into students/teachers)."""
    from .platform_footprint import build_footprint_school_rows

    q = (request.GET.get("q") or "").strip() or None
    _t, _s, _c, school_rows = build_footprint_school_rows(q=q)

    return render(
        request,
        "superadmin/schools_overview.html",
        {
            "school_rows": school_rows,
            "search_q": q or "",
        },
    )


@transaction.non_atomic_requests
@superadmin_required
def superadmin_school_students(request, school_id: int):
    """Per-school students list (card view) for super admin."""
    from .global_directory import collect_global_students, sort_student_rows

    school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
    status = (request.GET.get("status") or "").strip().lower()
    if status not in ("", "active", "inactive"):
        status = ""
    search = (request.GET.get("q") or "").strip()

    rows = collect_global_students(
        school_id=school_id,
        classroom_id=None,
        section_id=None,
        academic_year_name="",
        status=status,
        search=search,
        fee_filter="",
        today=timezone.localdate(),
    )
    sort_student_rows(rows, "name")
    paginator = Paginator(rows, 30)
    page = paginator.get_page(request.GET.get("page", 1))
    return render(
        request,
        "superadmin/school_students.html",
        {
            "school": school,
            "page_obj": page,
            "status": status,
            "search_q": search,
        },
    )


@transaction.non_atomic_requests
@superadmin_required
def superadmin_school_teachers(request, school_id: int):
    """Per-school teachers list (card view) for super admin."""
    from .global_directory import collect_global_teachers, sort_teacher_rows

    school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
    status = (request.GET.get("status") or "").strip().lower()
    if status not in ("", "active", "inactive"):
        status = ""
    search = (request.GET.get("q") or "").strip()

    rows = collect_global_teachers(
        school_id=school_id,
        subject_q="",
        status=status,
        search=search,
    )
    sort_teacher_rows(rows, "name")
    paginator = Paginator(rows, 30)
    page = paginator.get_page(request.GET.get("page", 1))
    return render(
        request,
        "superadmin/school_teachers.html",
        {
            "school": school,
            "page_obj": page,
            "status": status,
            "search_q": search,
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
        today=timezone.localdate(),
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


@transaction.non_atomic_requests
@superadmin_required
@require_http_methods(["POST"])
def superadmin_set_student_active(request):
    """Toggle a student user's active status within the selected tenant school."""
    from django_tenants.utils import tenant_context

    connection.set_schema_to_public()
    school_id = _parse_optional_int(request.POST.get("school_id"))
    student_pk = _parse_optional_int(request.POST.get("student_pk"))
    active = (request.POST.get("active") or "").strip().lower() == "1"
    next_url = (request.POST.get("next") or "").strip()
    if not school_id or not student_pk:
        messages.error(request, "Invalid request.")
        return redirect(next_url or "core:superadmin_global_students")

    school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
    try:
        with tenant_context(school):
            with transaction.atomic():
                from apps.school_data.models import Student

                st = Student.objects.select_related("user").filter(pk=student_pk).first()
                if not st or not st.user_id:
                    messages.error(request, "Student not found.")
                else:
                    st.user.is_active = active
                    st.user.save(update_fields=["is_active"])
                    messages.success(request, f"Student account set to {'Active' if active else 'Inactive'}.")
    except Exception as exc:
        messages.error(request, f"Could not update status: {exc}")
    return redirect(next_url or f"{reverse('core:superadmin_global_students')}?school={school_id}")


@transaction.non_atomic_requests
@superadmin_required
@require_http_methods(["POST"])
def superadmin_set_teacher_active(request):
    """Toggle a teacher user's active status within the selected tenant school."""
    from django_tenants.utils import tenant_context

    connection.set_schema_to_public()
    school_id = _parse_optional_int(request.POST.get("school_id"))
    teacher_pk = _parse_optional_int(request.POST.get("teacher_pk"))
    active = (request.POST.get("active") or "").strip().lower() == "1"
    next_url = (request.POST.get("next") or "").strip()
    if not school_id or not teacher_pk:
        messages.error(request, "Invalid request.")
        return redirect(next_url or "core:superadmin_global_teachers")

    school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
    try:
        with tenant_context(school):
            with transaction.atomic():
                from apps.school_data.models import Teacher

                t = Teacher.objects.select_related("user").filter(pk=teacher_pk).first()
                if not t or not t.user_id:
                    messages.error(request, "Teacher not found.")
                else:
                    t.user.is_active = active
                    t.user.save(update_fields=["is_active"])
                    messages.success(request, f"Teacher account set to {'Active' if active else 'Inactive'}.")
    except Exception as exc:
        messages.error(request, f"Could not update status: {exc}")
    return redirect(next_url or f"{reverse('core:superadmin_global_teachers')}?school={school_id}")


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
        "school",
        "recorded_by",
        "subscription",
        "subscription__plan",
        "school_generated_invoice",
    ).order_by("-payment_date", "-id")
    paginator = Paginator(qs, 40)
    page = paginator.get_page(request.GET.get("page", 1))
    today = timezone.localdate()
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
        initial = {"payment_date": timezone.localdate()}
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
        "school": None,
        "show_enrollment_welcome": False,
        "current_plan": None,
        "plan_name": "",
        "plan_display_name": None,
        "plan_features": [],
        "trial_active": False,
        "trial_days_left": None,
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
        "student_growth_trend": [],
        "show_student_growth_chart": False,
        "fee_collection_daily": [],
        "show_fee_daily_chart": False,
        "recent_activities": [],
        "dashboard_sparse": True,
        "admin_display_name": "",
        "today_iso": timezone.localdate().isoformat(),
    }
    if not school:
        return render(request, "core/dashboards/admin_dashboard.html", empty_ctx)
    if school.is_trial_expired():
        return render(request, "core/dashboards/trial_expired.html", {"school": school})

    ensure_tenant_for_request(request)

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
                    pending_fees += max(float(fee.effective_due_amount) - paid, 0.0)
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
        .order_by("grade_order", "name")
    )
    class_distribution = [{"label": x["name"], "count": x["cnt"]} for x in class_dist_qs if x["cnt"]]
    show_class_chart = bool(class_distribution)

    attendance_trend = []
    if has_attendance and total_students:
        for i in range(16, -1, -1):
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

    # Charts: student growth (last 6 months, new students per month) + fee bars (last 7 days)
    months_pair = []
    y, m = today.year, today.month
    for _ in range(6):
        months_pair.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months_pair.reverse()
    student_growth_trend = []
    for y, m in months_pair:
        n_new = Student.objects.filter(
            user__school=school,
            created_on__year=y,
            created_on__month=m,
        ).count()
        student_growth_trend.append(
            {
                "label": date(y, m, 1).strftime("%b %Y"),
                "short": date(y, m, 1).strftime("%b"),
                "count": n_new,
            }
        )
    show_student_growth_chart = True

    fee_collection_daily = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        amt = 0.0
        if has_fees:
            try:
                amt = float(
                    Payment.objects.filter(
                        payment_date=d,
                        fee__student__user__school=school,
                    ).aggregate(s=Sum("amount"))["s"]
                    or 0
                )
            except Exception:
                amt = 0.0
        fee_collection_daily.append(
            {
                "label": d.strftime("%d %b"),
                "short": d.strftime("%a"),
                "amount": amt,
            }
        )
    show_fee_daily_chart = has_fees

    # Recent activity feed (merged, sorted)
    recent_activities = []
    try:
        for s in (
            Student.objects.filter(user__school=school)
            .select_related("user")
            .order_by("-created_on")[:6]
        ):
            recent_activities.append(
                {
                    "ts": s.created_on,
                    "icon": "bi-person-plus",
                    "tone": "primary",
                    "text": f"Student enrolled: {s.user.get_full_name() or s.user.username}",
                }
            )
        for t in (
            Teacher.objects.filter(user__school=school)
            .select_related("user")
            .order_by("-created_on")[:5]
        ):
            recent_activities.append(
                {
                    "ts": t.created_on,
                    "icon": "bi-person-workspace",
                    "tone": "purple",
                    "text": f"Teacher added: {t.user.get_full_name() or t.user.username}",
                }
            )
        for sec in Section.objects.order_by("-created_on")[:4]:
            recent_activities.append(
                {
                    "ts": sec.created_on,
                    "icon": "bi-diagram-3",
                    "tone": "warning",
                    "text": f"Section created: {sec.name}",
                }
            )
        for cr in ClassRoom.objects.order_by("-created_on")[:4]:
            recent_activities.append(
                {
                    "ts": cr.created_on,
                    "icon": "bi-grid-3x3-gap",
                    "tone": "success",
                    "text": f"Class created: {cr.name}",
                }
            )
        for subj in Subject.objects.order_by("-created_on")[:4]:
            recent_activities.append(
                {
                    "ts": subj.created_on,
                    "icon": "bi-book",
                    "tone": "info",
                    "text": f"Subject added: {subj.name}",
                }
            )
        if has_fees:
            for pay in (
                Payment.objects.filter(fee__student__user__school=school)
                .select_related("fee__student__user")
                .order_by("-created_on")[:6]
            ):
                recent_activities.append(
                    {
                        "ts": pay.created_on,
                        "icon": "bi-currency-rupee",
                        "tone": "teal",
                        "text": f"Fee collected ₹{float(pay.amount):,.0f}",
                    }
                )
    except Exception:
        recent_activities = []
    recent_activities.sort(key=lambda x: x["ts"], reverse=True)
    recent_activities = recent_activities[:14]

    dashboard_sparse = (
        total_students == 0
        and total_teachers == 0
        and total_classes == 0
        and total_sections == 0
    )

    from apps.customers.subscription import PLAN_FEATURES

    sub_plan = school.billing_plan
    trial_active = (
        school.school_status == School.SchoolStatus.TRIAL
        and sub_plan
        and (sub_plan.name or "").lower() == "trial"
        and school.trial_end_date
        and school.trial_end_date >= today
    )
    trial_days_left = None
    if trial_active and school.trial_end_date:
        trial_days_left = max((school.trial_end_date - today).days, 0)

    saas = None
    plan_display_name = None
    if trial_active:
        current_plan = sub_plan
        plan_name = "trial"
        plan_display_name = "Free Trial"
        plan_features = list(
            dict.fromkeys(PLAN_FEATURES.get("pro", []) + PLAN_FEATURES.get("basic", []))
        )
    else:
        plan_name = (sub_plan.name if sub_plan else "").lower() or "basic"
        plan_features = PLAN_FEATURES.get(plan_name, [])
        current_plan = sub_plan

    show_enrollment_welcome = bool(request.session.pop("show_enrollment_welcome", False))

    return render(
        request,
        "core/dashboards/admin_dashboard.html",
        {
            "school": school,
            "show_enrollment_welcome": show_enrollment_welcome,
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
            "student_growth_trend": student_growth_trend,
            "show_student_growth_chart": show_student_growth_chart,
            "fee_collection_daily": fee_collection_daily,
            "show_fee_daily_chart": show_fee_daily_chart,
            "recent_activities": recent_activities,
            "dashboard_sparse": dashboard_sparse,
            "admin_display_name": (request.user.get_full_name() or "").strip()
            or getattr(request.user, "username", "")
            or "Admin",
            "today_iso": today.isoformat(),
            "current_plan": current_plan,
            "plan_name": plan_name,
            "plan_display_name": plan_display_name,
            "plan_features": plan_features,
            "trial_active": trial_active,
            "trial_days_left": trial_days_left,
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

    ensure_homework_enterprise_columns_if_missing(connection)
    ensure_homework_audit_columns_if_missing(connection)

    from .utils import teacher_class_section_pairs_display

    q = Q(teacher_id=teacher.pk) | Q(assigned_by_id=user.pk)

    pairs = list(
        ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
        .values_list("class_obj_id", "section_id")
        .distinct()
    )
    for cid, sid in pairs:
        q |= Q(classes__id=cid, sections__id=sid)

    # Same class–section scope as school admin "Assigned classes" (M2M), not only CSST rows.
    for cn, sn in teacher_class_section_pairs_display(teacher):
        if cn and sn:
            q |= Q(classes__name__iexact=cn, sections__name__iexact=sn)

    subj_ids = list(
        ClassSectionSubjectTeacher.objects.filter(teacher=teacher)
        .values_list("subject_id", flat=True)
        .distinct()
    )
    subj_ids.extend(teacher.subjects.values_list("id", flat=True))
    if teacher.subject_id:
        subj_ids.append(teacher.subject_id)
    subj_ids = list({x for x in subj_ids if x})
    if subj_ids:
        q |= Q(subject_id__in=subj_ids)

    base = Homework.objects.filter(q).defer("attachment")
    pk_subq = base.values("pk").distinct()
    return (
        Homework.objects.filter(pk__in=pk_subq)
        .defer("attachment")
        .select_related(
            "subject",
            "assigned_by",
            "teacher",
            "teacher__user",
            "academic_year",
            "modified_by",
        )
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
    classrooms = list(teacher.classrooms.all().order_by(*ORDER_GRADE_NAME))
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
            .defer(
                "session__updated_at",
                "session__display_order",
                "session__modified_by",
                "session__modified_at",
            )
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
    from apps.school_data.calendar_policy import portal_holiday_widget_context

    hol_ctx = portal_holiday_widget_context("teacher")
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
            **hol_ctx,
        },
    )


# ======================
# Student Dashboard
# ======================


@student_required
def student_dashboard(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        from apps.school_data.calendar_policy import portal_holiday_widget_context

        return render(
            request,
            "core/student_dashboard/dashboard.html",
            {
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
                "calendar_prev_disabled": True,
                "calendar_next_disabled": True,
                "calendar_today_month": timezone.localdate().month,
                "calendar_today_year": timezone.localdate().year,
                "calendar_showing_today_month": True,
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
                **portal_holiday_widget_context("student"),
            },
        )
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
    achievement_badges = _compute_student_achievement_badges(
        student=student,
        attendance_pct=attendance_pct,
        overall_pct=overall_pct,
    )

    cal_nav = _student_dashboard_calendar_nav(request, today, ay_start, ay_end)
    calendar_cells = _build_calendar_data(
        attendance_year_records,
        cal_nav["view_year"],
        cal_nav["view_month"],
        highlight_today=today,
    )
    if today < ay_start:
        cal_today_y, cal_today_m = ay_start.year, ay_start.month
    elif today > ay_end:
        cal_today_y, cal_today_m = ay_end.year, ay_end.month
    else:
        cal_today_y, cal_today_m = today.year, today.month
    calendar_showing_today_month = (
        cal_nav["view_year"] == cal_today_y and cal_nav["view_month"] == cal_today_m
    )

    # Analytics: Subject-wise % for latest exam (bar chart)
    subject_chart_labels = []
    subject_chart_data = []
    if marks_by_exam and latest_exam_name:
        for m in marks_by_exam[latest_exam_name]:
            pct = round((m.marks_obtained / m.total_marks * 100) if m.total_marks else 0, 1)
            subject_chart_labels.append(m.subject.name)
            subject_chart_data.append(pct)

    # Homework is loaded on the dedicated homework list page, not here (dashboard template
    # does not use this context; avoids hitting Homework ORM columns when a tenant schema
    # is missing migration 0045/0046/0049 columns such as assigned_date).
    homework = []

    today_classes = []
    try:
        from apps.timetable.views import today_classes_student
        today_classes = today_classes_student(student)
    except Exception:
        pass
    from apps.school_data.calendar_policy import portal_holiday_widget_context

    # Attendance trend (monthly % within academic year window) — for dashboard line chart
    # Use up to last 6 months present in records (keeps chart compact).
    month_buckets = {}
    for r in attendance_year_records:
        key = (r.date.year, r.date.month)
        b = month_buckets.get(key)
        if not b:
            b = {"present": 0, "total": 0}
            month_buckets[key] = b
        b["total"] += 1
        if r.status == "PRESENT":
            b["present"] += 1
    month_keys_sorted = sorted(month_buckets.keys())
    month_keys_sorted = month_keys_sorted[-6:]
    attendance_trend_labels = [date(y, m, 1).strftime("%b %Y") for (y, m) in month_keys_sorted]
    attendance_trend_data = [
        round((month_buckets[(y, m)]["present"] / month_buckets[(y, m)]["total"] * 100), 1)
        if month_buckets[(y, m)]["total"]
        else 0
        for (y, m) in month_keys_sorted
    ]

    return render(request, "core/student_dashboard/dashboard.html", {
        "attendance_list": list(attendance_year_qs.order_by("-date")),
        "total_days": total_att,
        "present_days": present_att,
        "attendance_percentage": attendance_pct,
        "academic_year": academic_year_label,
        "attendance_heatmap": attendance_heatmap,
        "calendar_month_label": date(cal_nav["view_year"], cal_nav["view_month"], 1).strftime("%B %Y"),
        "calendar_cells": calendar_cells,
        "calendar_prev_month": cal_nav["prev_month"],
        "calendar_prev_year": cal_nav["prev_year"],
        "calendar_next_month": cal_nav["next_month"],
        "calendar_next_year": cal_nav["next_year"],
        "calendar_prev_disabled": cal_nav["prev_disabled"],
        "calendar_next_disabled": cal_nav["next_disabled"],
        "calendar_today_month": cal_today_m,
        "calendar_today_year": cal_today_y,
        "calendar_showing_today_month": calendar_showing_today_month,
        "current_streak": current_streak,
        "best_streak": best_streak,
        "achievement_badges": achievement_badges,
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
        "attendance_trend_labels": attendance_trend_labels,
        "attendance_trend_data": attendance_trend_data,
        "today_classes": today_classes,
        **portal_holiday_widget_context("student"),
    })


@student_required
def student_profile(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied

    extra = student.extra_data or {}

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
            homework_streak, _ = Badge.objects.get_or_create(
                name="Homework Streak 🔥",
                defaults={"description": "Awarded for 5+ consecutive homework submission days.", "icon": "bi bi-fire"},
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
        # Homework streak (based on attempt log)
        try:
            hw_days = list(
                HomeworkSubmissionAttempt.objects.filter(student=_student)
                .exclude(submitted_at__isnull=True)
                .order_by("-submitted_at")
                .values_list("submitted_at", flat=True)[:40]
            )
            hw_day_dates = [dt.date() for dt in hw_days if dt]
            if _distinct_day_streak(hw_day_dates) >= 5:
                to_award.append(homework_streak)
        except Exception:
            pass

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
            "extra": extra,
            "extra_basic": (extra.get("basic", {}) or {}),
            "extra_academic": (extra.get("academic", {}) or {}),
            "extra_parents": (extra.get("parents", {}) or {}),
            "extra_contact": (extra.get("contact", {}) or {}),
            "extra_medical": (extra.get("medical", {}) or {}),
            "extra_th": (extra.get("transport_hostel", {}) or {}),
            "extra_billing": (extra.get("billing", {}) or {}),
            "extra_status": (extra.get("status", {}) or {}),
            "profile_completion": profile_completion,
            "attendance_percentage": attendance_percentage,
            "total_exams": total_exams,
            "avg_marks": avg_marks,
            "badges": badges,
        },
    )


@student_required
def edit_profile(request):
    # Backward-compatible route: redirect to the new full profile settings page.
    return redirect("core:student_profile_settings")


@student_required
def student_profile_settings(request):
    """
    Student self-service profile page.
    Shows all relevant fields (including extra_data) and allows editing only safe fields.
    """
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    if getattr(student, "user_id", None) != getattr(request.user, "id", None):
        raise PermissionDenied

    from .forms import StudentProfileForm

    extra = student.extra_data or {}
    basic = extra.get("basic", {}) or {}
    parents = extra.get("parents", {}) or {}
    contact = extra.get("contact", {}) or {}
    medical = extra.get("medical", {}) or {}

    initial = {
        "_request_user_id": request.user.id,
        # Locked identifiers (display only)
        "username": request.user.username or "",
        "admission_number": student.admission_number or "",
        "roll_number": student.roll_number or "",
        "classroom": getattr(student.classroom, "name", "") if student.classroom else "",
        "section": getattr(student.section, "name", "") if student.section else "",
        "academic_year": getattr(student.academic_year, "name", "") if student.academic_year else "",
        # User personal
        "first_name": request.user.first_name or "",
        "last_name": request.user.last_name or "",
        # Student core
        "date_of_birth": student.date_of_birth,
        "gender": student.gender or "",
        "student_mobile": student.phone or "",
        "address_line1": (student.address or "").split("\n")[0] if student.address else "",
        "address_line2": (student.address or "").split("\n")[1] if student.address and "\n" in student.address else "",
        "email": request.user.email or "",
        # Basic (extra_data)
        "blood_group": basic.get("blood_group") or "",
        "id_number": basic.get("id_number") or "",
        "nationality": basic.get("nationality") or "",
        "religion": basic.get("religion") or "",
        "mother_tongue": basic.get("mother_tongue") or "",
        # Parents (extra_data + a couple top-level convenience fields)
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
        "student_email": parents.get("student_email") or "",
        # Contact (extra_data)
        "city": contact.get("city") or "",
        "district": contact.get("district") or "",
        "state": contact.get("state") or "",
        "pincode": contact.get("pincode") or "",
        "country": contact.get("country") or "",
        # Medical (extra_data)
        "emergency_contact_name": medical.get("emergency_contact_name") or "",
        "emergency_phone": medical.get("emergency_phone") or "",
        "allergies": medical.get("allergies") or "",
        "medical_conditions": medical.get("medical_conditions") or "",
        "doctor_name": medical.get("doctor_name") or "",
        "hospital": medical.get("hospital") or "",
        "insurance_details": medical.get("insurance_details") or "",
    }

    form = StudentProfileForm(
        data=(request.POST or None),
        files=(request.FILES or None),
        initial=initial,
    )

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data

        changed = False

        # User account email + name (allowed)
        user_update_fields = []
        new_email = (data.get("email") or "").strip()
        if (request.user.email or "") != new_email:
            request.user.email = new_email
            user_update_fields.append("email")
        new_first = (data.get("first_name") or "").strip()
        if (request.user.first_name or "") != new_first:
            request.user.first_name = new_first
            user_update_fields.append("first_name")
        new_last = (data.get("last_name") or "").strip()
        if (request.user.last_name or "") != new_last:
            request.user.last_name = new_last
            user_update_fields.append("last_name")
        if user_update_fields:
            request.user.save(update_fields=user_update_fields)
            changed = True

        # Student model: safe updates only
        new_dob = data.get("date_of_birth")
        if student.date_of_birth != new_dob:
            student.date_of_birth = new_dob
            changed = True

        new_gender = data.get("gender") or ""
        if (student.gender or "") != new_gender:
            student.gender = new_gender
            changed = True

        new_address = (
            "\n".join([data.get("address_line1") or "", data.get("address_line2") or ""]).strip() or None
        )
        if (student.address or None) != new_address:
            student.address = new_address
            changed = True

        new_parent_name = (data.get("parent_name") or "").strip()
        if (student.parent_name or "") != new_parent_name:
            student.parent_name = new_parent_name
            changed = True

        new_parent_phone = (data.get("parent_phone") or "").strip()
        if (student.parent_phone or "") != new_parent_phone:
            student.parent_phone = new_parent_phone
            changed = True

        if data.get("profile_image"):
            student.profile_image = data.get("profile_image")
            changed = True

        # Student.extra_data: merge into existing keys; do not overwrite academic/system blocks
        merged = dict(extra or {})
        merged_basic = dict(basic or {})
        merged_parents = dict(parents or {})
        merged_contact = dict(contact or {})
        merged_medical = dict(medical or {})

        new_basic_payload = {
            "blood_group": data.get("blood_group") or "",
            "id_number": data.get("id_number") or "",
            "nationality": data.get("nationality") or "",
            "religion": data.get("religion") or "",
            "mother_tongue": data.get("mother_tongue") or "",
        }
        for k, v in new_basic_payload.items():
            if (merged_basic.get(k) or "") != v:
                changed = True
            merged_basic[k] = v

        new_parents_payload = {
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
        }
        for k, v in new_parents_payload.items():
            if (merged_parents.get(k) or "") != v:
                changed = True
            merged_parents[k] = v

        new_contact_payload = {
            "city": data.get("city") or "",
            "district": data.get("district") or "",
            "state": data.get("state") or "",
            "pincode": data.get("pincode") or "",
            "country": data.get("country") or "",
        }
        for k, v in new_contact_payload.items():
            if (merged_contact.get(k) or "") != v:
                changed = True
            merged_contact[k] = v

        new_medical_payload = {
            "emergency_contact_name": data.get("emergency_contact_name") or "",
            "emergency_phone": data.get("emergency_phone") or "",
            "allergies": data.get("allergies") or "",
            "medical_conditions": data.get("medical_conditions") or "",
            "doctor_name": data.get("doctor_name") or "",
            "hospital": data.get("hospital") or "",
            "insurance_details": data.get("insurance_details") or "",
        }
        for k, v in new_medical_payload.items():
            if (merged_medical.get(k) or "") != v:
                changed = True
            merged_medical[k] = v

        merged["basic"] = merged_basic
        merged["parents"] = merged_parents
        merged["contact"] = merged_contact
        merged["medical"] = merged_medical
        if student.extra_data != merged:
            student.extra_data = merged
            changed = True

        if not changed:
            messages.info(
                request,
                "No editable changes to save.",
            )
            return redirect("core:student_profile_settings")

        student.save()
        messages.success(request, "Profile updated successfully.")
        return redirect("core:student_profile_settings")

    ctx = {
        "student": student,
        "form": form,
        "readonly": {
            "username": request.user.username,
            "email": request.user.email,
            "admission_number": student.admission_number,
            "roll_number": student.roll_number,
            "academic_year": getattr(student.academic_year, "name", "") if student.academic_year else "",
            "classroom": getattr(student.classroom, "name", "") if student.classroom else "",
            "section": getattr(student.section, "name", "") if student.section else "",
        },
        "extra_data": extra,
    }
    return render(request, "core/student_dashboard/profile_settings.html", ctx)


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


def _default_grade_policy() -> list[dict]:
    """
    Min % is inclusive lower bound; next higher band caps the range.
    F: 0–34%, E: 35–50%, D: 51–59%, C: 60–69%, B: 70–79%, A: 80–89%, A+: 90%+.
    """
    return [
        {"grade": "A+", "min_pct": 90, "enabled": True},
        {"grade": "A", "min_pct": 80, "enabled": True},
        {"grade": "B", "min_pct": 70, "enabled": True},
        {"grade": "C", "min_pct": 60, "enabled": True},
        {"grade": "D", "min_pct": 51, "enabled": True},
        {"grade": "E", "min_pct": 35, "enabled": True},
        {"grade": "F", "min_pct": 0, "enabled": True},
    ]


def _grade_policy_key_for_school(school) -> str:
    return f"school:{school.id}:grading_policy"


def _get_grade_policy_for_school(school) -> list[dict]:
    """
    Per-school grading policy stored in public schema `PlatformSettings` as JSON.
    If missing/invalid, falls back to default policy.
    """
    if not school:
        return _default_grade_policy()
    try:
        from apps.customers.models import PlatformSettings

        row = PlatformSettings.objects.filter(key=_grade_policy_key_for_school(school)).first()
        raw = (row.value or {}) if row else {}
        bands = raw.get("bands")
        if not isinstance(bands, list) or not bands:
            return _default_grade_policy()
        cleaned = []
        for b in bands:
            if not isinstance(b, dict):
                continue
            g = (b.get("grade") or "").strip()
            try:
                mp = int(b.get("min_pct"))
            except Exception:
                continue
            if not g:
                continue
            if mp < 0:
                mp = 0
            if mp > 100:
                mp = 100
            en = b.get("enabled", True)
            if isinstance(en, str):
                en = en.strip().lower() in ("1", "true", "yes", "on")
            cleaned.append({"grade": g, "min_pct": mp, "enabled": bool(en)})
        cleaned.sort(key=lambda x: x["min_pct"], reverse=True)
        return cleaned or _default_grade_policy()
    except Exception:
        return _default_grade_policy()


def _active_grade_bands(bands: list[dict]) -> list[dict]:
    """Only enabled bands, sorted by min_pct descending (highest threshold first)."""
    out = []
    for b in bands or []:
        if b.get("enabled", True) is False:
            continue
        out.append(b)
    out.sort(key=lambda x: int(x.get("min_pct", 0)), reverse=True)
    return out


def _grade_from_pct_with_policy(pct: float | None, bands: list[dict]) -> str:
    if pct is None:
        return "—"
    try:
        p = float(pct)
    except Exception:
        return "—"
    for b in _active_grade_bands(bands):
        try:
            if p >= float(b.get("min_pct", 0)):
                return str(b.get("grade") or "").strip() or "—"
        except Exception:
            continue
    return "—"


def _grade_tooltip_map(bands: list[dict]) -> dict:
    """
    Map grade -> human tooltip (enabled bands only). Ranges use the next enabled higher min − 0.1.
    """
    cleaned = []
    for b in _active_grade_bands(bands):
        g = (b.get("grade") or "").strip()
        try:
            mp = float(b.get("min_pct"))
        except Exception:
            continue
        if g:
            cleaned.append((g, mp))
    cleaned.sort(key=lambda x: x[1], reverse=True)
    out = {}
    for i, (g, mp) in enumerate(cleaned):
        if i == 0:
            out[g] = f"{g}: {int(mp)}% and above"
        else:
            upper = cleaned[i - 1][1] - 0.1
            out[g] = f"{g}: {int(mp)}% – {upper:.1f}%"
    return out


def _grading_policy_signature(bands: list[dict]) -> tuple:
    """Stable compare for 'matches system default' (grade, min_pct, enabled)."""
    norm = []
    for b in bands or []:
        norm.append(
            (
                str(b.get("grade") or "").strip().upper(),
                int(b.get("min_pct") or 0),
                bool(b.get("enabled", True)),
            )
        )
    return tuple(sorted(norm, key=lambda x: (-x[1], x[0])))


@admin_required
@require_GET
def school_settings_index(request):
    """School admin: settings landing page."""
    return render(
        request,
        "core/school/settings/index.html",
        {
            "items": [
                {
                    "title": "Master Dropdown Settings",
                    "desc": "Manage reusable dropdown values used across Student, Teacher, Admissions, Attendance and Reports.",
                    "href": reverse("core:school_master_dropdown_settings"),
                    "icon": "bi-sliders",
                },
                {
                    "title": "Grading System",
                    "desc": "Configure grade boundaries (percent-based).",
                    "href": reverse("core:school_grading_settings"),
                    "icon": "bi-award",
                },
            ]
        },
    )


@admin_required
@require_http_methods(["GET", "POST"])
def school_grading_settings(request):
    """School admin: configure grade boundaries (percent-based)."""
    school = getattr(request.user, "school", None)
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")

    default_bands = _default_grade_policy()
    bands = _get_grade_policy_for_school(school)
    is_default = _grading_policy_signature(bands) == _grading_policy_signature(default_bands)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "reset_default":
            new_bands = [dict(b) for b in default_bands]
        else:
            grades = request.POST.getlist("grade")
            mins = request.POST.getlist("min_pct")
            new_bands = []
            for i, (g, m) in enumerate(zip(grades, mins, strict=False)):
                gg = (g or "").strip()
                if not gg:
                    continue
                try:
                    mp = int(str(m).strip())
                except Exception:
                    mp = 0
                mp = max(0, min(100, mp))
                enabled = request.POST.get(f"enabled_{i}") == "1"
                new_bands.append({"grade": gg, "min_pct": mp, "enabled": enabled})
            if not new_bands:
                new_bands = [dict(b) for b in default_bands]
            if all(int(b.get("min_pct") or 0) != 0 for b in new_bands):
                new_bands.append({"grade": "F", "min_pct": 0, "enabled": True})
            if not any(b.get("enabled", True) for b in new_bands):
                messages.error(request, "Enable at least one grade.")
                return redirect("core:school_grading_settings")
            new_bands.sort(key=lambda x: int(x.get("min_pct") or 0), reverse=True)

        try:
            from apps.customers.models import PlatformSettings

            PlatformSettings.objects.update_or_create(
                key=_grade_policy_key_for_school(school),
                defaults={"value": {"bands": new_bands}},
            )
            messages.success(request, "Grading system updated.")
        except Exception:
            messages.error(request, "Could not save grading settings. Please try again.")
        return redirect("core:school_grading_settings")

    return render(
        request,
        "core/school/settings/grading.html",
        {
            "bands": bands,
            "is_default": is_default,
            "preview": _grade_tooltip_map(bands),
        },
    )


@admin_required
@require_GET
def school_master_dropdown_settings(request):
    """Settings: manage tenant master dropdown options (MasterDataOption)."""
    selected_key = (request.GET.get("key") or "").strip() or MasterDataOption.Key.GENDER
    valid_keys = {k for k, _ in MasterDataOption.Key.choices}
    if selected_key not in valid_keys:
        selected_key = MasterDataOption.Key.GENDER

    q = (request.GET.get("q") or "").strip()
    category = (request.GET.get("category") or "").strip()

    # Segment definitions for *tenant* dropdown values (forms, admissions, etc.).
    # Platform-wide analytics/report *registry* is public-schema AnalyticsField — not edited here.
    categories = [
        ("common", "Common fields", [
            "gender", "blood_group", "nationality", "religion", "caste_category", "mother_tongue",
            "marital_status", "transport_required", "status", "attendance_status",
        ]),
        ("student", "Student fields", [
            "admission_source", "fee_category", "student_status", "previous_board", "medium_of_instruction",
            "admission_status",
        ]),
        ("teacher", "Teacher / staff fields", [
            "staff_type", "designation", "department", "qualification", "employment_type", "shift",
            "reporting_manager", "experience_level", "payroll_category",
        ]),
        ("parent", "Parent fields", [
            "relationship", "occupation", "annual_income_range", "education_level",
        ]),
    ]

    cat_order = [ck for ck, _, _ in categories]
    cat_title_short = {
        "common": "Common",
        "student": "Students",
        "teacher": "Staff",
        "parent": "Parents",
    }

    # key -> which segment slugs apply (only keys that exist on the model)
    key_to_segments = {k: [] for k in valid_keys}
    for ck, _, keys in categories:
        for raw_k in keys:
            if raw_k not in valid_keys:
                continue
            short = cat_title_short.get(ck, ck)
            if short not in key_to_segments[raw_k]:
                key_to_segments[raw_k].append(short)

    # Primary segment for sorting / optgroups: first category block in order that lists this key
    key_primary_cat = {}
    for ck in cat_order:
        for _ck, _, keys in categories:
            if _ck != ck:
                continue
            for raw_k in keys:
                if raw_k in valid_keys and raw_k not in key_primary_cat:
                    key_primary_cat[raw_k] = ck

    for k in valid_keys:
        key_primary_cat.setdefault(k, "common")

    choices_dict = dict(MasterDataOption.Key.choices)

    def passes_filters(k: str) -> bool:
        if category:
            in_cat = False
            for ck, _, keys in categories:
                if ck != category:
                    continue
                if k in keys:
                    in_cat = True
                    break
            if not in_cat:
                return False
        if q:
            ql = q.lower()
            label = choices_dict.get(k) or ""
            if ql not in k.lower() and ql not in label.lower():
                seg_blob = " ".join(key_to_segments.get(k, [])).lower()
                if ql not in seg_blob:
                    return False
        return True

    filtered_keys = [k for k in choices_dict if passes_filters(k)]
    filtered_keys.sort(
        key=lambda k: (cat_order.index(key_primary_cat.get(k, "common")), (choices_dict[k] or "").lower())
    )

    def option_row(k: str):
        label = choices_dict[k]
        seg = " · ".join(key_to_segments.get(k, []))
        return (k, label, seg)

    keys_choices = [option_row(k) for k in filtered_keys]

    # When browsing all segments, group the Field key dropdown by primary segment (optgroups).
    field_key_groups = None
    if not category:
        field_key_groups = []
        for ck, cl, _ in categories:
            row_keys = [k for k in filtered_keys if key_primary_cat.get(k) == ck]
            if not row_keys:
                continue
            field_key_groups.append((cl, [option_row(k) for k in row_keys]))

    selected_segments = " · ".join(key_to_segments.get(selected_key, []))

    options = MasterDataOption.objects.filter(key=selected_key).order_by("display_order", "name")

    return render(
        request,
        "core/school/settings/master_dropdowns.html",
        {
            "selected_key": selected_key,
            "selected_segments": selected_segments,
            "keys_choices": keys_choices,
            "field_key_groups": field_key_groups,
            "categories": categories,
            "category": category,
            "q": q,
            "options": options,
        },
    )


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


def _build_calendar_data(
    records,
    year: int,
    month: int,
    *,
    highlight_today: date | None = None,
):
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
            label = "No record"
        is_today = highlight_today is not None and cur == highlight_today
        title = f"{cur.isoformat()} — {label}"
        if is_today:
            title += " (today)"
        cells.append(
            {
                "is_blank": False,
                "day": day_num,
                "css": css,
                "label": label,
                "title": title,
                "is_today": is_today,
            }
        )

    # Pad to complete final week row
    while len(cells) % 7 != 0:
        cells.append({"is_blank": True})
    return cells


def _student_dashboard_calendar_nav(request, today: date, ay_start: date, ay_end: date) -> dict:
    """
    Month/year for the attendance mini-calendar from GET (?month=&year=), clamped to the
    academic year. Prev/next targets and disabled flags when hitting AY bounds.
    """
    ay_first = date(ay_start.year, ay_start.month, 1)
    ay_last = date(ay_end.year, ay_end.month, 1)

    def _default_ym():
        t_first = date(today.year, today.month, 1)
        if t_first < ay_first:
            return ay_first.year, ay_first.month
        if t_first > ay_last:
            return ay_last.year, ay_last.month
        return today.year, today.month

    raw_m = (request.GET.get("month") or "").strip()
    raw_y = (request.GET.get("year") or "").strip()
    if raw_m == "" or raw_y == "":
        y, m = _default_ym()
    else:
        try:
            m = int(raw_m)
            y = int(raw_y)
        except (TypeError, ValueError):
            y, m = _default_ym()
        else:
            if not (1 <= m <= 12 and 1900 <= y <= 2100):
                y, m = _default_ym()

    first = date(y, m, 1)
    if first < ay_first:
        y, m = ay_first.year, ay_first.month
    elif first > ay_last:
        y, m = ay_last.year, ay_last.month

    if m == 1:
        py, pm = y - 1, 12
    else:
        py, pm = y, m - 1
    prev_disabled = date(py, pm, 1) < ay_first

    if m == 12:
        ny, nm = y + 1, 1
    else:
        ny, nm = y, m + 1
    next_disabled = date(ny, nm, 1) > ay_last

    return {
        "view_year": y,
        "view_month": m,
        "prev_year": py,
        "prev_month": pm,
        "next_year": ny,
        "next_month": nm,
        "prev_disabled": prev_disabled,
        "next_disabled": next_disabled,
    }


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

    # Trend chart (use full filtered range, not just current page)
    trend_rows = list(qs.order_by("date").values_list("date", "status"))
    trend_labels = []
    trend_pct = []
    cum_total = 0
    cum_present = 0
    for dt, st in trend_rows:
        cum_total += 1
        if st == "PRESENT":
            cum_present += 1
        pct_val = round((cum_present / cum_total * 100) if cum_total else 0, 2)
        trend_labels.append(dt.isoformat())
        trend_pct.append(pct_val)

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
    calendar_cells = (
        _build_calendar_data(records, year_int, month_int, highlight_today=timezone.localdate())
        if view_type == "monthly"
        else []
    )
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
        "trend_labels": trend_labels,
        "trend_pct": trend_pct,
    })


def _student_exam_summaries(student):
    """
    Build summary list for the student: one row per exam *session* (all subjects aggregated),
    plus one row per legacy standalone exam paper (no session).
    """
    bands = _get_grade_policy_for_school(getattr(getattr(student, "user", None), "school", None))
    exams = []
    exam_marks = (
        Marks.objects.filter(student=student, exam__isnull=False)
        .select_related("exam", "exam__session", "subject")
        .defer(
            "exam__session__updated_at",
            "exam__session__display_order",
            "exam__session__modified_by",
            "exam__session__modified_at",
        )
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
            "total_obtained": total_o,
            "total_marks": total_m,
            "overall_pct": pct,
            "grade": _grade_from_pct_with_policy(pct, bands),
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
            "total_obtained": total_o,
            "total_marks": total_m,
            "overall_pct": pct,
            "grade": _grade_from_pct_with_policy(pct, bands),
            "has_marks": total_m > 0,
        })

    # Scheduled exam sessions for this class–section (no marks yet)
    if student.classroom and student.section:
        cn = student.classroom.name
        sn = student.section.name
        from_marks_session_ids = {e["session_id"] for e in exams if e.get("is_session") and e.get("session_id")}
        scheduled = (
            _examsession_queryset()
            .filter(class_name__iexact=cn, section__iexact=sn)
            .annotate(
                paper_count=Count("papers", distinct=True),
                dmin=Min("papers__date"),
                dmax=Max("papers__date"),
            )
            .filter(paper_count__gt=0)
        )
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
                "total_obtained": None,
                "total_marks": None,
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
    bands = _get_grade_policy_for_school(getattr(request.user, "school", None))
    tooltip_map = _grade_tooltip_map(bands)
    # Overall should be based on published marks only (pending sessions are skipped).
    agg = (
        Marks.objects.filter(student=student, exam__isnull=False)
        .aggregate(
            total_obtained=Sum("marks_obtained"),
            total_marks=Sum("total_marks"),
        )
    )
    total_o = float(agg.get("total_obtained") or 0)
    total_m = float(agg.get("total_marks") or 0)
    overall_pct = round((total_o / total_m * 100), 1) if total_m else 0.0
    for e in exams:
        g = e.get("grade")
        e["grade_tooltip"] = tooltip_map.get(g or "", "")
    skipped = sum(1 for e in exams if e.get("overall_pct") is None)
    return render(
        request,
        "core/student/exams_list.html",
        {
            "exams": exams,
            "overall_pct": overall_pct,
            "overall_grade": _grade_from_pct_with_policy(overall_pct, bands),
            "overall_grade_tooltip": tooltip_map.get(_grade_from_pct_with_policy(overall_pct, bands), ""),
            "overall_skipped_count": skipped,
            "overall_has_any_result": bool(total_m),
            "grade_tooltips": tooltip_map,
        },
    )


def _examsession_queryset():
    """Defer columns some tenant DBs lack until migrations 0039/0041/0043 are applied."""
    return ExamSession.objects.defer(
        "updated_at",
        "display_order",
        "modified_by",
        "modified_at",
    )


def _can_manage_exam_session_admin_actions(user) -> bool:
    """School admin and platform superadmin may edit/delete exam sessions (UI + API parity)."""
    role = getattr(user, "role", None)
    return role in (User.Roles.ADMIN, User.Roles.SUPERADMIN)


@student_required
def student_exam_session_detail(request, session_id):
    """Student: schedule + marks for all papers under one exam session."""
    if not has_feature_access(getattr(request.user, "school", None), "exams", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    student = getattr(request.user, "student_profile", None)
    if not student or not student.classroom or not student.section:
        raise PermissionDenied
    session_obj = get_object_or_404(
        _examsession_queryset().select_related("classroom"),
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
    bands = _get_grade_policy_for_school(getattr(request.user, "school", None))
    tooltip_map = _grade_tooltip_map(bands)
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
        g = _grade_from_pct_with_policy(pct, bands) if pct is not None else "—"
        schedule_rows.append({
            "paper": p,
            "subject": p.subject.name if p.subject else "—",
            "date": p.date,
            "start_time": p.start_time,
            "end_time": p.end_time,
            "mark": mk,
            "pct": pct,
            "grade": g,
            "grade_tooltip": tooltip_map.get(g, ""),
        })
    overall_pct = round((total_o / total_m * 100), 1) if total_m else None
    overall_grade = _grade_from_pct_with_policy(overall_pct, bands) if overall_pct is not None else "—"
    # Marks completeness: report card should be shown only when all papers have marks.
    marks_complete = bool(papers) and all((marks_by_exam_id.get(p.id) is not None) for p in papers)
    return render(
        request,
        "core/student/exam_session_detail.html",
        {
            "session": session_obj,
            "schedule_rows": schedule_rows,
            "overall_pct": overall_pct,
            "grade": overall_grade,
            "overall_grade_tooltip": tooltip_map.get(overall_grade, ""),
            "grade_tooltips": tooltip_map,
            "marks_complete": marks_complete,
            "show_incomplete_marks_modal": (request.GET.get("marks_incomplete") or "").strip() in ("1", "true", "yes"),
            "total_obtained": total_o if total_m else None,
            "total_max": total_m if total_m else None,
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

    # Attendance trend granularity: daily | weekly | monthly
    att_granularity = (request.GET.get("att_granularity") or "monthly").strip().lower()
    if att_granularity not in ("daily", "weekly", "monthly"):
        att_granularity = "monthly"

    att_labels = []
    att_values = []
    try:
        # Use calendar policy to compute average over WORKING days:
        # pct = present_working_days / total_working_days * 100
        from apps.school_data.calendar_policy import resolve_day

        ay_obj = get_active_academic_year_obj()
        if ay_obj and getattr(ay_obj, "start_date", None) and getattr(ay_obj, "end_date", None):
            ay_start, ay_end = ay_obj.start_date, ay_obj.end_date
        else:
            ay_obj = None
            ay_start, ay_end = get_current_academic_year_bounds()
        # Map recorded statuses by date for quick lookup
        status_by_date = {
            d: s for (d, s) in att_qs.filter(date__gte=ay_start, date__lte=ay_end).values_list("date", "status")
        }

        if att_granularity == "daily":
            cur = ay_start
            while cur <= ay_end:
                if resolve_day(cur, "student", ay=ay_obj).is_working_day:
                    st = status_by_date.get(cur)
                    att_labels.append(cur.isoformat())
                    att_values.append(100.0 if st == Attendance.Status.PRESENT else 0.0)
                cur = date.fromordinal(cur.toordinal() + 1)
        elif att_granularity == "weekly":
            weekly = {}
            cur = ay_start
            while cur <= ay_end:
                if resolve_day(cur, "student", ay=ay_obj).is_working_day:
                    iso_year, iso_week, _ = cur.isocalendar()
                    key = (iso_year, iso_week)
                    if key not in weekly:
                        weekly[key] = {"present": 0, "total": 0}
                    weekly[key]["total"] += 1
                    if status_by_date.get(cur) == Attendance.Status.PRESENT:
                        weekly[key]["present"] += 1
                cur = date.fromordinal(cur.toordinal() + 1)
            for (iso_year, iso_week) in sorted(weekly.keys()):
                data = weekly[(iso_year, iso_week)]
                pct = round((data["present"] / data["total"] * 100) if data["total"] else 0, 1)
                att_labels.append(f"W{iso_week} {iso_year}")
                att_values.append(pct)
        else:
            # monthly
            monthly = {}
            cur = date(ay_start.year, ay_start.month, 1)
            end_month = date(ay_end.year, ay_end.month, 1)
            while cur <= end_month:
                monthly[(cur.year, cur.month)] = {"present": 0, "total": 0}
                cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
            cur = ay_start
            while cur <= ay_end:
                if resolve_day(cur, "student", ay=ay_obj).is_working_day:
                    key = (cur.year, cur.month)
                    if key not in monthly:
                        monthly[key] = {"present": 0, "total": 0}
                    monthly[key]["total"] += 1
                    if status_by_date.get(cur) == Attendance.Status.PRESENT:
                        monthly[key]["present"] += 1
                cur = date.fromordinal(cur.toordinal() + 1)
            from calendar import month_name
            for (y, m) in sorted(monthly.keys()):
                data = monthly[(y, m)]
                if data["total"] <= 0:
                    continue
                att_labels.append(f"{month_name[m]} {y}")
                att_values.append(round((data["present"] / data["total"] * 100), 1))
    except Exception:
        att_labels = []
        att_values = []

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
    subject_chart_map = {}
    for s in subject_stats.values():
        pct = round((s["obtained"] / s["max"] * 100) if s["max"] else 0, 1)
        subject_chart_labels.append(s["subject"].name)
        subject_chart_values.append(pct)
        subject_chart_map[s["subject"].name] = pct

    overall_pct = round((total_obtained / total_max * 100) if total_max else 0, 1)

    # Performance trend based on last two exams
    # Insights (trend + best/weak + suggestion)
    insights = {
        "trend_state": "N/A",  # Improving | Declining | Stable | N/A
        "trend_arrow": "",
        "trend_delta": None,
        "trend_note": "",
        "best_subject": strongest[1].name if strongest else None,
        "best_subject_pct": round(strongest[0], 1) if strongest else None,
        "weak_subjects": [],
        "suggestion": "",
    }

    # Subject averages
    subj_items = []
    for s in subject_stats.values():
        pct = round((s["obtained"] / s["max"] * 100) if s["max"] else 0, 1)
        subj_items.append({"name": s["subject"].name, "pct": pct})
    subj_items.sort(key=lambda x: x["pct"])
    insights["weak_subjects"] = [x for x in subj_items[:3] if x.get("name")]

    if insights["weak_subjects"]:
        weakest_name = insights["weak_subjects"][0]["name"]
        insights["suggestion"] = f"Focus on {weakest_name} to improve your overall score."
    elif overall_pct and overall_pct < 60:
        insights["suggestion"] = "Focus on revising fundamentals and solving past papers to improve your overall score."
    else:
        insights["suggestion"] = "Keep going—practice consistently to maintain and improve your performance."

    # Trend using last 2–3 published exams by date
    trend_source = [
        e
        for e in exams
        if e.get("overall_pct") is not None and (e.get("exam_date") is not None or e.get("date_max") is not None)
    ]
    trend_source.sort(key=lambda e: (e.get("exam_date") or e.get("date_max") or date.min))
    last3 = trend_source[-3:]
    if len(last3) >= 2:
        last_pct = float(last3[-1].get("overall_pct") or 0)
        prev_pcts = [float(x.get("overall_pct") or 0) for x in last3[:-1]]
        prev_avg = sum(prev_pcts) / len(prev_pcts) if prev_pcts else last_pct
        delta = round(last_pct - prev_avg, 1)
        insights["trend_delta"] = delta
        if delta > 0.5:
            insights["trend_state"] = "Improving"
            insights["trend_arrow"] = "↑"
        elif delta < -0.5:
            insights["trend_state"] = "Declining"
            insights["trend_arrow"] = "↓"
        else:
            insights["trend_state"] = "Stable"
            insights["trend_arrow"] = "→"
        insights["trend_note"] = f"Compared to your previous {len(prev_pcts)} exam(s)."

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
            "trend": insights["trend_state"],
        },
        "summary": {
            "overall_pct": overall_pct,
            "total_exams": len([e for e in exams if e.get("exam_name")]),
            "best_subject": strongest[1].name if strongest else None,
            "lowest_subject": weakest[1].name if weakest else None,
        },
        "insights": insights,
        "exam_trend_labels": exam_trend_labels,
        "exam_trend_values": exam_trend_values,
        "subject_chart_labels": subject_chart_labels,
        "subject_chart_values": subject_chart_values,
        "subject_chart_map": subject_chart_map,
        "attendance_trend_labels": att_labels,
        "attendance_trend_values": att_values,
        "att_granularity": att_granularity,
    }
    return render(request, "core/student/reports.html", context)


@student_required
@feature_required("reports")
def student_calendar(request):
    """
    Unified student calendar: Exams + Homework deadlines + Holidays/Events.
    Monthly grid with color-coded items.
    """
    student = getattr(request.user, "student_profile", None)
    if not student or not student.classroom:
        raise PermissionDenied

    # Month navigation (default: current month)
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
        month = int(request.GET.get("month") or today.month)
    except Exception:
        year, month = today.year, today.month
    if month < 1 or month > 12:
        month = today.month
    if year < 2000 or year > 2100:
        year = today.year

    from calendar import monthrange, month_name
    from apps.school_data.calendar_policy import academic_year_for_date, get_holiday_calendar_for_year, build_month_cells

    month_start = date(year, month, 1)
    month_end = date(year, month, monthrange(year, month)[1])

    # Holiday calendar
    ay = get_active_academic_year_obj() or academic_year_for_date(month_start)
    cal = get_holiday_calendar_for_year(ay) if ay else None
    cells = build_month_cells(year, month, cal, audience="student")

    # Events mapping: iso date -> list of {type,label,url}
    events_by_iso = {}

    def _add(d: date, payload: dict):
        key = d.isoformat()
        events_by_iso.setdefault(key, []).append(payload)

    # Homework due dates (visible to student)
    hw_statuses = [Homework.Status.PUBLISHED, Homework.Status.CLOSED]
    hw_qs = (
        Homework.objects.filter(status__in=hw_statuses)
        .filter(due_date__gte=month_start, due_date__lte=month_end)
        .defer("attachment")
        .select_related("subject")
        .order_by("due_date", "id")
    )
    # Scope: new (classes+sections) or legacy (subject mapped to class+section)
    if student.section_id:
        hw_qs = hw_qs.filter(
            Q(classes=student.classroom, sections=student.section)
            | Q(
                subject_id__in=ClassSectionSubjectTeacher.objects.filter(
                    class_obj=student.classroom,
                    section=student.section,
                ).values_list("subject_id", flat=True)
            )
        ).distinct()
    else:
        hw_qs = Homework.objects.none()
    for hw in hw_qs:
        _add(
            hw.due_date,
            {
                "kind": "homework",
                "label": f"HW: {hw.title}",
                "url": reverse("core:student_homework_detail", args=[hw.id]),
            },
        )

    # Exams (papers) for student's class+section, in this month
    ex_qs = (
        Exam.objects.filter(date__gte=month_start, date__lte=month_end)
        .filter(class_name__iexact=(student.classroom.name or ""))
        .filter(section__iexact=(getattr(student.section, "name", "") or ""))
        .select_related("subject", "session")
        .order_by("date", "start_time", "id")
    )
    for ex in ex_qs:
        subj = ex.subject.name if ex.subject else (ex.name or "Exam")
        if ex.session_id:
            url = reverse("core:student_exam_session_detail", args=[ex.session_id])
        else:
            url = reverse("core:student_exam_detail_by_id", args=[ex.id])
        _add(
            ex.date,
            {
                "kind": "exam",
                "label": f"Exam: {subj}",
                "url": url,
            },
        )

    # Holidays/events (published calendar only; policy already handles Sunday label)
    if cal and getattr(cal, "is_published", False) and ay:
        evs = (
            HolidayEvent.objects.filter(calendar=cal)
            .filter(start_date__gte=month_start, start_date__lte=month_end)
            .order_by("start_date", "name")
        )
        for ev in evs:
            if ev.applies_to in (HolidayEvent.AppliesTo.BOTH, HolidayEvent.AppliesTo.STUDENTS):
                _add(
                    ev.start_date,
                    {
                        "kind": "holiday",
                        "label": ev.name,
                        "url": "",
                    },
                )

    # Prev/next month links
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    # Attach items to cells so templates don't need dict indexing helpers
    for c in cells:
        if not c.get("is_blank"):
            c["items"] = events_by_iso.get(c.get("iso") or "", [])

    return render(
        request,
        "core/student/calendar.html",
        {
            "student": student,
            "today": today,
            "year": year,
            "month": month,
            "month_label": f"{month_name[month]} {year}",
            "prev": {"year": prev_year, "month": prev_month},
            "next": {"year": next_year, "month": next_month},
            "cells": cells,
            "weekdays": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        },
    )


@student_required
def student_resources(request):
    """
    Student resources hub: notes, PDFs, videos, assignments (file or external URL).
    """
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied

    qs = StudentResource.objects.filter(is_active=True).select_related("subject").order_by("-created_at", "-id")

    subject_id = (request.GET.get("subject") or "").strip()
    rtype = (request.GET.get("type") or "").strip().upper()

    if subject_id.isdigit():
        qs = qs.filter(subject_id=int(subject_id))
    type_choices = [c[0] for c in StudentResource.ResourceType.choices]
    if rtype in type_choices:
        qs = qs.filter(resource_type=rtype)
    else:
        rtype = ""

    subject_choices = list(
        Subject.objects.filter(id__in=qs.exclude(subject__isnull=True).values_list("subject_id", flat=True).distinct())
        .order_by("display_order", "name")
    )

    resources = list(qs[:200])  # keep it fast; add pagination later if needed

    return render(
        request,
        "core/student/resources.html",
        {
            "resources": resources,
            "filters": {"subject": subject_id, "type": rtype},
            "subject_choices": subject_choices,
            "type_choices": StudentResource.ResourceType.choices,
        },
    )


@student_required
@feature_required("reports")
@require_http_methods(["GET", "POST"])
def student_messages(request):
    """
    Student ↔ Teacher messaging (inbox + thread).
    """
    student = getattr(request.user, "student_profile", None)
    if not student or not student.classroom or not student.section:
        raise PermissionDenied

    def _allowed_teacher_rows(_student):
        """
        Return (teacher_users, teacher_rows, allowed_teacher_ids) for this student using CSST mapping.
        teacher_rows include subjects_display + unread + active.
        """
        from apps.school_data.models import TeacherClassSection
        from apps.timetable.models import Timetable

        csst = (
            ClassSectionSubjectTeacher.objects.filter(class_obj=_student.classroom, section=_student.section)
            .select_related("teacher__user", "subject")
            .order_by("teacher_id", "subject__display_order", "subject__name")
        )
        teacher_subjects = {}
        teacher_user_map = {}
        for row in csst:
            tu = getattr(row.teacher, "user", None)
            if not tu:
                continue
            teacher_user_map[tu.id] = tu
            if row.subject and row.subject.name:
                teacher_subjects.setdefault(tu.id, set()).add(row.subject.name)

        # Fallback: if CSST mappings aren't configured, allow teachers assigned to this class+section.
        if not teacher_user_map:
            # 0) Timetable-based subject teachers (most real-world setups use timetable first)
            try:
                tt = (
                    Timetable.objects.filter(classroom=_student.classroom, subject__isnull=False)
                    .select_related("subject")
                    .prefetch_related("teachers__user")
                )
                for entry in tt:
                    subj_name = getattr(getattr(entry, "subject", None), "name", None)
                    for t in entry.teachers.all():
                        tu = getattr(t, "user", None)
                        if not tu:
                            continue
                        teacher_user_map[tu.id] = tu
                        if subj_name:
                            teacher_subjects.setdefault(tu.id, set()).add(subj_name)
            except Exception:
                pass

            # 1) Section-level assignments (teacher edit page "Assigned sections")
            tcs = (
                TeacherClassSection.objects.filter(classroom=_student.classroom, section=_student.section)
                .select_related("teacher__user")
                .order_by("teacher_id")
            )
            for a in tcs:
                tu = getattr(getattr(a, "teacher", None), "user", None)
                if tu:
                    teacher_user_map[tu.id] = tu

            # 2) Homeroom/class-teacher assignment (class edit "teacher per section")
            try:
                hr = ClassSectionTeacher.objects.filter(class_obj=_student.classroom, section=_student.section).select_related(
                    "teacher__user"
                ).first()
                tu = getattr(getattr(hr, "teacher", None), "user", None) if hr else None
                if tu:
                    teacher_user_map[tu.id] = tu
            except Exception:
                pass

        allowed_teacher_ids = set(teacher_user_map.keys())
        teacher_users = list(
            User.objects.filter(id__in=list(allowed_teacher_ids)).order_by("first_name", "username")
        )

        # Last message preview per teacher (for WhatsApp-style sidebar list).
        last_map = {}
        try:
            if allowed_teacher_ids:
                msg_qs = (
                    StudentMessage.objects.filter(student=_student)
                    .filter(
                        (Q(sender=request.user) & Q(receiver_id__in=list(allowed_teacher_ids)))
                        | (Q(receiver=request.user) & Q(sender_id__in=list(allowed_teacher_ids)))
                    )
                    .only("id", "created_at", "content", "sender_id", "receiver_id")
                    .order_by("-created_at", "-id")
                )
                for m in msg_qs[:1500]:
                    other_id = m.receiver_id if m.sender_id == request.user.id else m.sender_id
                    if other_id in last_map:
                        continue
                    prev = (m.content or "").strip().replace("\n", " ")
                    last_map[other_id] = {
                        "ts": m.created_at,
                        "preview": (prev[:180] + "…") if len(prev) > 180 else prev,
                    }
        except Exception:
            last_map = {}

        unread_map = {
            uid: c
            for uid, c in StudentMessage.objects.filter(student=_student, receiver=request.user, is_read=False)
            .values_list("sender_id")
            .annotate(c=Count("id"))
        }
        rows = []
        for u in teacher_users:
            subj = sorted(list(teacher_subjects.get(u.id, set())))
            last = last_map.get(u.id) or {}
            rows.append(
                {
                    "user": u,
                    "subjects_display": ", ".join(subj) if subj else "",
                    "unread": int(unread_map.get(u.id) or 0),
                    "last_ts": last.get("ts"),
                    "last_preview": last.get("preview", "") or "",
                    "active": False,
                }
            )
        return teacher_users, rows, allowed_teacher_ids

    teacher_users, teacher_rows, allowed_teacher_ids = _allowed_teacher_rows(student)
    show_all = (request.GET.get("all") or "").strip() == "1"

    # Mark "sent" -> "delivered" for any messages received by this user (inbox opened).
    try:
        now = timezone.now()
        StudentMessage.objects.filter(student=student, receiver=request.user, status="sent").update(
            status="delivered",
            delivered_at=now,
        )
    except Exception:
        pass

    # Default: keep the chat list compact (no scroll) by showing only the latest chats.
    if not show_all:
        try:
            teacher_rows = sorted(
                teacher_rows,
                key=lambda r: (
                    1 if r.get("last_ts") else 0,
                    r.get("last_ts") or timezone.datetime.min.replace(tzinfo=timezone.get_current_timezone()),
                    (getattr(getattr(r.get("user"), "get_full_name", None), "__call__", None) and r["user"].get_full_name())
                    or getattr(r.get("user"), "username", "")
                    or "",
                ),
                reverse=True,
            )[:6]
        except Exception:
            teacher_rows = teacher_rows[:6]

    active_teacher_id = (request.GET.get("teacher") or "").strip()
    active_teacher = None
    if active_teacher_id.isdigit() and int(active_teacher_id) in allowed_teacher_ids:
        active_teacher = next((u for u in teacher_users if u.id == int(active_teacher_id)), None)

    from .forms import StudentTeacherMessageForm

    # If the UI selected a teacher via URL but the form POST didn't include it (header dropdown is outside the form),
    # fall back to the active teacher from query param so validation succeeds.
    form_data = request.POST or None
    if request.method == "POST" and form_data is not None:
        try:
            if (form_data.get("teacher") or "").strip() == "" and active_teacher:
                form_data = form_data.copy()
                form_data["teacher"] = str(active_teacher.id)
        except Exception:
            form_data = request.POST or None

    form = StudentTeacherMessageForm(
        data=form_data,
        teacher_qs=User.objects.filter(id__in=list(allowed_teacher_ids)).order_by("first_name", "username"),
    )
    if request.method != "POST" and active_teacher:
        # Preselect current thread teacher in the dropdown.
        try:
            form.fields["teacher"].initial = active_teacher.id
        except Exception:
            pass

    if request.method == "POST":
        if not form.is_valid():
            messages.error(request, "Please select a valid teacher and type a message.")
            # keep current thread selected if possible
            if active_teacher:
                return redirect(reverse("core:student_messages") + f"?teacher={active_teacher.id}")
            return redirect("core:student_messages")
        active_teacher = form.cleaned_data["teacher"]
        content = (form.cleaned_data.get("content") or "").strip()

        StudentMessage.objects.create(
            sender=request.user,
            receiver=active_teacher,
            student=student,
            subject=None,
            content=content,
            status="sent",
        )
        return redirect(reverse("core:student_messages") + f"?teacher={active_teacher.id}")

    thread = []
    if active_teacher:
        thread = list(
            StudentMessage.objects.filter(student=student)
            .filter(
                Q(sender=request.user, receiver=active_teacher)
                | Q(sender=active_teacher, receiver=request.user)
            )
            .select_related("sender", "receiver")
            .order_by("created_at", "id")
        )
        try:
            now = timezone.now()
            StudentMessage.objects.filter(
                student=student,
                sender=active_teacher,
                receiver=request.user,
                status__in=["sent", "delivered"],
            ).update(status="seen", seen_at=now, is_read=True)
            StudentMessage.objects.filter(
                student=student, sender=active_teacher, receiver=request.user, is_read=False
            ).update(is_read=True)
        except Exception:
            StudentMessage.objects.filter(
                student=student, sender=active_teacher, receiver=request.user, is_read=False
            ).update(is_read=True)

    for r in teacher_rows:
        r["active"] = bool(active_teacher and r["user"].id == active_teacher.id)

    return render(
        request,
        "core/student/messages.html",
        {
            "student": student,
            "teachers": teacher_rows,
            "active_teacher": active_teacher,
            "thread": thread,
            "form": form,
            "show_all": show_all,
        },
    )


@student_required
@require_GET
def student_messages_api_teachers(request):
    student = getattr(request.user, "student_profile", None)
    if not student or not student.classroom or not student.section:
        raise PermissionDenied
    from apps.school_data.models import TeacherClassSection

    # Build allowed teachers + subjects (CSST logic; with fallback to section assignments)
    csst = (
        ClassSectionSubjectTeacher.objects.filter(class_obj=student.classroom, section=student.section)
        .select_related("teacher__user", "subject")
        .order_by("teacher_id", "subject__display_order", "subject__name")
    )
    m = {}
    for row in csst:
        tu = getattr(row.teacher, "user", None)
        if not tu:
            continue
        rec = m.get(tu.id)
        if not rec:
            rec = {"id": tu.id, "name": tu.get_full_name() or tu.username, "username": tu.username, "subjects": []}
            m[tu.id] = rec
        if row.subject and row.subject.name and row.subject.name not in rec["subjects"]:
            rec["subjects"].append(row.subject.name)

    if not m:
        # Fallback: teacher assignments for this class+section (no subject mapping)
        tcs = (
            TeacherClassSection.objects.filter(classroom=student.classroom, section=student.section)
            .select_related("teacher__user")
            .order_by("teacher_id")
        )
        for a in tcs:
            tu = getattr(getattr(a, "teacher", None), "user", None)
            if not tu:
                continue
            rec = m.get(tu.id)
            if not rec:
                rec = {"id": tu.id, "name": tu.get_full_name() or tu.username, "username": tu.username, "subjects": []}
                m[tu.id] = rec
        try:
            hr = ClassSectionTeacher.objects.filter(class_obj=student.classroom, section=student.section).select_related(
                "teacher__user"
            ).first()
            tu = getattr(getattr(hr, "teacher", None), "user", None) if hr else None
            if tu and tu.id not in m:
                m[tu.id] = {"id": tu.id, "name": tu.get_full_name() or tu.username, "username": tu.username, "subjects": []}
        except Exception:
            pass
    teachers = list(m.values())
    teachers.sort(key=lambda x: (x["name"] or "", x["id"]))
    unread_map = {
        uid: c
        for uid, c in StudentMessage.objects.filter(student=student, receiver=request.user, is_read=False)
        .values_list("sender_id")
        .annotate(c=Count("id"))
    }
    for t in teachers:
        t["unread"] = int(unread_map.get(t["id"]) or 0)
        t["subjects_display"] = ", ".join(t["subjects"])
    return JsonResponse({"teachers": teachers})


@student_required
@require_GET
def student_messages_api_thread(request):
    student = getattr(request.user, "student_profile", None)
    if not student or not student.classroom or not student.section:
        raise PermissionDenied
    tid = (request.GET.get("teacher") or "").strip()
    if not tid.isdigit():
        return JsonResponse({"messages": []})
    teacher_user_id = int(tid)
    allowed = ClassSectionSubjectTeacher.objects.filter(
        class_obj=student.classroom, section=student.section, teacher__user_id=teacher_user_id
    ).exists()
    if not allowed:
        return JsonResponse({"messages": []}, status=403)
    teacher_user = User.objects.filter(id=teacher_user_id).first()
    if not teacher_user:
        return JsonResponse({"messages": []})
    qs = (
        StudentMessage.objects.filter(student=student)
        .filter(Q(sender=request.user, receiver=teacher_user) | Q(sender=teacher_user, receiver=request.user))
        .order_by("created_at", "id")
    )
    msgs = []
    for m in qs[:300]:
        msgs.append(
            {
                "id": m.id,
                "from_me": m.sender_id == request.user.id,
                "content": m.content,
                "ts": timezone.localtime(m.created_at).strftime("%b %d, %I:%M %p") if m.created_at else "",
            }
        )
    try:
        now = timezone.now()
        StudentMessage.objects.filter(
            student=student,
            sender=teacher_user,
            receiver=request.user,
            status__in=["sent", "delivered"],
        ).update(status="seen", seen_at=now, is_read=True)
        StudentMessage.objects.filter(student=student, sender=teacher_user, receiver=request.user, is_read=False).update(
            is_read=True
        )
    except Exception:
        StudentMessage.objects.filter(student=student, sender=teacher_user, receiver=request.user, is_read=False).update(
            is_read=True
        )
    return JsonResponse({"messages": msgs})


@student_required
@require_POST
def student_messages_api_send(request):
    student = getattr(request.user, "student_profile", None)
    if not student or not student.classroom or not student.section:
        raise PermissionDenied
    tid = (request.POST.get("teacher") or "").strip()
    content = (request.POST.get("content") or "").strip()
    if not tid.isdigit() or not content:
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)
    teacher_user_id = int(tid)
    allowed = ClassSectionSubjectTeacher.objects.filter(
        class_obj=student.classroom, section=student.section, teacher__user_id=teacher_user_id
    ).exists()
    if not allowed:
        return JsonResponse({"ok": False, "error": "Teacher not allowed."}, status=403)
    teacher_user = User.objects.filter(id=teacher_user_id).first()
    if not teacher_user:
        return JsonResponse({"ok": False, "error": "Teacher not found."}, status=404)
    msg = StudentMessage.objects.create(
        sender=request.user,
        receiver=teacher_user,
        student=student,
        subject=None,
        content=content,
        status="sent",
    )
    return JsonResponse(
        {
            "ok": True,
            "message": {
                "id": msg.id,
                "from_me": True,
                "content": msg.content,
                "ts": timezone.localtime(msg.created_at).strftime("%b %d, %I:%M %p") if msg.created_at else "",
            },
        }
    )


@teacher_required
@feature_required("reports")
@require_http_methods(["GET", "POST"])
def teacher_messages(request):
    """Teacher messaging center (students + school admin)."""
    teacher = getattr(request.user, "teacher_profile", None)
    if not teacher:
        raise PermissionDenied

    # Mark "sent" -> "delivered" for any incoming messages (inbox opened).
    try:
        now = timezone.now()
        StudentMessage.objects.filter(receiver=request.user, status="sent").update(status="delivered", delivered_at=now)
        from apps.notifications.models import Message as InternalMessage

        InternalMessage.objects.filter(receiver=request.user, status="sent").update(status="delivered", delivered_at=now)
    except Exception:
        pass

    # Students teacher is allowed to message (used for auth + active chat).
    allowed_students_qs = (
        Student.objects.filter(classroom__in=teacher.classrooms.all())
        .select_related("user", "classroom", "section")
        .distinct()
    )
    allowed_student_ids = set(allowed_students_qs.values_list("id", flat=True))

    # Broadcast targeting data (class -> sections -> counts + small preview list).
    broadcast_classes = list(teacher.classrooms.all().order_by("grade_order", "name"))
    broadcast_sections_rows = list(
        allowed_students_qs.values("classroom_id", "classroom__name", "section_id", "section__name")
        .annotate(count=Count("id"))
        .order_by("classroom__grade_order", "classroom__name", "section__name")
    )
    # Build JSON-safe payload for the modal (class -> sections -> counts + preview names).
    _sections_by_class: dict[int, list[dict]] = {}
    for r in broadcast_sections_rows:
        cid = r["classroom_id"]
        sid = r["section_id"]
        if not cid or not sid:
            continue
        # Small preview list: first 12 student names in that class+section.
        preview_qs = (
            allowed_students_qs.filter(classroom_id=cid, section_id=sid)
            .select_related("user")
            .order_by("user__first_name", "user__last_name", "user__username")[:12]
        )
        preview = [s.user.get_full_name() or s.user.username for s in preview_qs if getattr(s, "user", None)]
        _sections_by_class.setdefault(int(cid), []).append(
            {
                "id": int(sid),
                "name": r["section__name"] or "—",
                "count": int(r["count"] or 0),
                "preview": preview,
            }
        )

    broadcast_payload = {
        "classes": [
            {
                "id": int(c.id),
                "name": c.name,
                "sections": _sections_by_class.get(int(c.id), []),
            }
            for c in broadcast_classes
        ]
    }

    # Client-side broadcast/search payload (all allowed students, not only "recent").
    allowed_students_payload = []
    for s in allowed_students_qs.order_by("user__first_name", "user__last_name", "user__username"):
        u = getattr(s, "user", None)
        if not u:
            continue
        name = u.get_full_name() or u.username
        cls = getattr(getattr(s, "classroom", None), "name", "") or ""
        sec = getattr(getattr(s, "section", None), "name", "") or ""
        rn = getattr(s, "roll_number", "") or ""
        allowed_students_payload.append(
            {
                "id": int(s.id),
                "name": name,
                "classroom": cls,
                "section": sec,
                "roll_number": rn,
                "hay": f"{name} {cls} {sec} {rn} {u.username}".lower(),
            }
        )

    # WhatsApp-style sidebar: show *recent conversations* (not all students).
    last_msg_qs = (
        StudentMessage.objects.filter(student_id=OuterRef("pk"))
        .filter(Q(sender=request.user) | Q(receiver=request.user))
        .order_by("-created_at", "-id")
    )
    recent_students_qs = (
        allowed_students_qs.annotate(
            last_ts=Subquery(last_msg_qs.values("created_at")[:1]),
            last_preview=Subquery(last_msg_qs.values("content")[:1]),
            last_sender_id=Subquery(last_msg_qs.values("sender_id")[:1]),
        )
        .filter(last_ts__isnull=False)
        .order_by("-last_ts")
    )
    show_all = (request.GET.get("all") or "").strip() == "1"
    recent_students = list(recent_students_qs[:60])
    admin_user = (
        User.objects.filter(
            role__in=[User.Roles.ADMIN, User.Roles.SUPERADMIN],
            school=getattr(request.user, "school", None),
            is_active=True,
        )
        .exclude(id=request.user.id)
        .order_by("role", "first_name", "username")
        .first()
    )

    peer_type = (request.GET.get("peer_type") or "student").strip().lower()
    peer_id = (request.GET.get("peer_id") or "").strip()
    active_student = None
    active_admin = None
    if peer_type == "admin":
        if admin_user and (not peer_id or str(admin_user.id) == peer_id):
            active_admin = admin_user
            peer_id = str(admin_user.id)
        else:
            peer_id = ""
    else:
        peer_type = "student"
        if peer_id.isdigit() and int(peer_id) in allowed_student_ids:
            # `recent_students` is a subset; fetch from allowed qs if needed.
            active_student = next((s for s in recent_students if s.id == int(peer_id)), None) or allowed_students_qs.filter(
                id=int(peer_id)
            ).first()
        else:
            peer_id = ""

    if request.method == "POST":
        target_type = (request.POST.get("peer_type") or "student").strip().lower()
        target_id = (request.POST.get("peer_id") or "").strip()
        content = (request.POST.get("content") or "").strip()
        if not content:
            messages.error(request, "Please type a message.")
            return redirect("core:teacher_messages")

        if target_type == "broadcast":
            class_id = (request.POST.get("classroom_id") or "").strip()
            section_ids = request.POST.getlist("section_ids")

            if not class_id.isdigit():
                messages.error(request, "Please select a class for broadcast.")
                return redirect("core:teacher_messages")
            class_id_int = int(class_id)
            if not any(c.id == class_id_int for c in broadcast_classes):
                messages.error(request, "You are not allowed to broadcast to this class.")
                return redirect("core:teacher_messages")

            recipients_qs = allowed_students_qs.filter(classroom_id=class_id_int).select_related("user")
            section_ids_int = []
            for s in section_ids:
                if str(s).isdigit():
                    section_ids_int.append(int(s))
            if section_ids_int:
                recipients_qs = recipients_qs.filter(section_id__in=section_ids_int)

            recipients = list(recipients_qs)
            if not recipients:
                messages.warning(request, "No students found for the selected class/sections.")
                return redirect("core:teacher_messages")

            now = timezone.now()
            rows = []
            for s in recipients:
                if not getattr(s, "user_id", None):
                    continue
                rows.append(
                    StudentMessage(
                        sender=request.user,
                        receiver=s.user,
                        student=s,
                        subject=None,
                        content=content,
                        created_at=now,
                    )
                )
            try:
                with transaction.atomic():
                    StudentMessage.objects.bulk_create(rows, batch_size=500)
            except DatabaseError:
                messages.error(request, "Broadcast failed due to a database error. Please try again.")
                return redirect("core:teacher_messages")

            messages.success(request, f"Broadcast sent to {len(rows)} students.")
            return redirect("core:teacher_messages")

        if target_type == "broadcast_ids":
            # Teacher -> selected students (ids must be subset of allowed_student_ids).
            student_ids_raw = request.POST.getlist("student_ids")
            ids = []
            for v in student_ids_raw:
                if str(v).isdigit():
                    ids.append(int(v))
            ids = list({i for i in ids if i in allowed_student_ids})
            if not ids:
                messages.error(request, "No valid students selected for broadcast.")
                return JsonResponse({"ok": False, "error": "No valid students selected."}, status=400)

            recipients = list(allowed_students_qs.filter(id__in=ids).select_related("user"))
            now = timezone.now()
            rows = []
            for s in recipients:
                if not getattr(s, "user_id", None):
                    continue
                rows.append(
                    StudentMessage(
                        sender=request.user,
                        receiver=s.user,
                        student=s,
                        subject=None,
                        content=content,
                        created_at=now,
                    )
                )
            try:
                with transaction.atomic():
                    StudentMessage.objects.bulk_create(rows, batch_size=500)
            except DatabaseError:
                return JsonResponse({"ok": False, "error": "Database error."}, status=500)

            return JsonResponse({"ok": True, "sent": len(rows)})

        if target_type == "admin":
            if not admin_user or not target_id.isdigit() or int(target_id) != admin_user.id:
                messages.error(request, "School admin is not available.")
                return redirect("core:teacher_messages")
            try:
                # Use a savepoint: if the admin-chat schema is missing/broken,
                # don't poison the whole request transaction.
                with transaction.atomic():
                    InternalMessage.objects.create(
                        school=request.user.school,
                        sender=request.user,
                        receiver=admin_user,
                        content=content,
                    )
            except (ProgrammingError, OperationalError, DatabaseError):
                messages.error(
                    request,
                    "Admin messaging table is not ready yet. Please run notifications migrations.",
                )
                return redirect("core:teacher_messages")
            return redirect(reverse("core:teacher_messages") + f"?peer_type=admin&peer_id={admin_user.id}")

        if not target_id.isdigit() or int(target_id) not in allowed_student_ids:
            messages.error(request, "Please select a valid student.")
            return redirect("core:teacher_messages")
        target_student = allowed_students_qs.filter(id=int(target_id)).first()
        if not target_student:
            messages.error(request, "Selected student not found.")
            return redirect("core:teacher_messages")
        StudentMessage.objects.create(
            sender=request.user,
            receiver=target_student.user,
            student=target_student,
            subject=None,
            content=content,
        )
        return redirect(reverse("core:teacher_messages") + f"?peer_type=student&peer_id={target_student.id}")

    thread = []
    if active_admin:
        try:
            # Guard each admin-chat DB operation with a savepoint. Without this,
            # a single admin-chat schema/query error can abort the whole request
            # transaction in PostgreSQL.
            with transaction.atomic():
                thread = list(
                    InternalMessage.objects.filter(
                        Q(sender=request.user, receiver=active_admin) | Q(sender=active_admin, receiver=request.user)
                    )
                    .select_related("sender", "receiver")
                    .order_by("timestamp", "id")
                )
            with transaction.atomic():
                InternalMessage.objects.filter(sender=active_admin, receiver=request.user, is_read=False).update(
                    is_read=True
                )
        except (ProgrammingError, OperationalError, DatabaseError):
            thread = []
            messages.warning(
                request,
                "Admin chat table is not available yet. Run notifications migrations to enable it.",
            )
    elif active_student:
        thread = list(
            StudentMessage.objects.filter(student=active_student)
            .filter(
                Q(sender=request.user, receiver=active_student.user)
                | Q(sender=active_student.user, receiver=request.user)
            )
            .select_related("sender", "receiver")
            .order_by("created_at", "id")
        )
        StudentMessage.objects.filter(
            student=active_student, sender=active_student.user, receiver=request.user, is_read=False
        ).update(is_read=True)

    unread_student_map = {
        sid: c
        for sid, c in StudentMessage.objects.filter(receiver=request.user, is_read=False)
        .values_list("student_id")
        .annotate(c=Count("id"))
    }
    unread_admin_count = 0
    if admin_user:
        try:
            with transaction.atomic():
                unread_admin_count = (
                    InternalMessage.objects.filter(sender=admin_user, receiver=request.user, is_read=False).count()
                )
        except (ProgrammingError, OperationalError, DatabaseError):
            unread_admin_count = 0

    # If teacher opened a student chat that isn't "recent" yet, still show it in the sidebar.
    if active_student and all(s.id != active_student.id for s in recent_students):
        recent_students.insert(0, active_student)

    if not show_all:
        recent_students = recent_students[:6]

    student_rows = []
    for s in recent_students:
        student_rows.append(
            {
                "obj": s,
                "unread": int(unread_student_map.get(s.id, 0)),
                "last_ts": getattr(s, "last_ts", None),
                "last_preview": getattr(s, "last_preview", "") or "",
                "last_sender_id": getattr(s, "last_sender_id", None),
            }
        )

    return render(
        request,
        "core/teacher/messages.html",
        {
            "teacher": teacher,
            "student_rows": student_rows,
            "admin_user": admin_user,
            "active_student": active_student,
            "active_admin": active_admin,
            "active_peer_type": "admin" if active_admin else "student",
            "thread": thread,
            "unread_student_map": unread_student_map,
            "unread_admin_count": unread_admin_count,
            "broadcast_classes": broadcast_classes,
            "broadcast_payload": broadcast_payload,
            "allowed_students_payload": allowed_students_payload,
            "show_all": show_all,
        },
    )


@teacher_required
@feature_required("reports")
@require_GET
def teacher_students_search_api(request):
    """
    API: search students a teacher is allowed to message (not only recent chats).
    Used by teacher messages sidebar search to start a new conversation.
    """
    teacher = getattr(request.user, "teacher_profile", None)
    if not teacher:
        raise PermissionDenied

    q = (request.GET.get("q") or "").strip()
    if not q:
        return JsonResponse({"ok": True, "results": []})

    allowed_qs = (
        Student.objects.filter(classroom__in=teacher.classrooms.all())
        .select_related("user", "classroom", "section")
        .distinct()
    )

    # Tokenize for more forgiving partial matches.
    tokens = [t for t in q.split() if t][:5]
    cond = Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q) | Q(user__username__icontains=q)
    cond |= Q(roll_number__icontains=q) | Q(classroom__name__icontains=q) | Q(section__name__icontains=q)
    for t in tokens:
        cond |= Q(user__first_name__icontains=t) | Q(user__last_name__icontains=t) | Q(user__username__icontains=t)
        cond |= Q(roll_number__icontains=t) | Q(classroom__name__icontains=t) | Q(section__name__icontains=t)

    qs = allowed_qs.filter(cond).order_by("user__first_name", "user__last_name", "user__username")[:25]
    out = []
    for s in qs:
        u = getattr(s, "user", None)
        if not u:
            continue
        out.append(
            {
                "id": int(s.id),
                "name": u.get_full_name() or u.username,
                "classroom": getattr(getattr(s, "classroom", None), "name", "") or "",
                "section": getattr(getattr(s, "section", None), "name", "") or "",
                "roll_number": getattr(s, "roll_number", "") or "",
            }
        )

    return JsonResponse({"ok": True, "results": out})


@student_required
def student_announcements(request):
    """
    Student announcements / notices (simple list).
    """
    if not has_feature_access(getattr(request.user, "school", None), "reports", user=request.user):
        # Keep it accessible even if reports feature toggled; announcements are lightweight.
        pass
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied

    qs = StudentAnnouncement.objects.filter(is_active=True).order_by("-publish_at", "-created_at", "-id")
    rows = list(qs[:60])
    return render(
        request,
        "core/student/announcements.html",
        {
            "student": student,
            "announcements": rows,
        },
    )


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
        _examsession_queryset().select_related("classroom"),
        pk=session_id,
    )
    if not student.classroom or not student.section:
        raise PermissionDenied
    if (
        student.classroom.name != session_obj.class_name
        or student.section.name != session_obj.section
    ):
        raise PermissionDenied
    # Prevent report card if marks are not fully entered for this session.
    papers = list(
        _exam_papers_full_qs()
        .filter(session=session_obj)
        .order_by("date", "subject__name")
    )
    marks_by_exam_id = {
        m.exam_id: m
        for m in Marks.objects.filter(student=student, exam__session=session_obj).select_related("exam")
    }
    if papers and any((marks_by_exam_id.get(p.id) is None) for p in papers):
        messages.warning(
            request,
            "Marks are not fully entered yet. Please contact your teacher.",
        )
        # Signal the session page to show a modal (also keeps it user-friendly without relying on toasts).
        return redirect(reverse("core:student_exam_session_detail", args=[session_obj.id]) + "?marks_incomplete=1")
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
    session_obj = get_object_or_404(_examsession_queryset(), pk=session_id)
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

    # Ensure we are bound to the correct tenant schema before any ORM runs.
    # Without this, some requests can hit the public schema (or an unbound connection)
    # and then fail later during template rendering with "relation does not exist".
    ensure_tenant_for_request(request)
    try:
        from apps.core.tenant_schema_repair import recover_db_connection_for_request

        recover_db_connection_for_request(request, close_connection=False)
    except Exception:
        pass

    # Hard guard: if core tenant tables are missing, do not pass lazy querysets to templates.
    # Use information_schema (not search_path dependent) to detect missing tables safely.
    try:
        from apps.core.db_schema_utils import missing_tables

        schema = getattr(connection, "schema_name", None) or getattr(school, "schema_name", "") or ""
        miss = missing_tables(
            schema,
            (
                "school_data_classroom",
                "school_data_section",
                "school_data_student",
                "school_data_academicyear",
            ),
        ) if schema else []
    except Exception:
        miss = []
    if miss:
        hint = tenant_migrate_cli_hint(school)
        err_msg = (
            "Required tables are missing in this school's database schema. "
            "That usually means tenant migrations were not applied for this school. "
            f"Run the following on the server, then reload this page:\n\n{hint}"
        )
        empty_filters = {
            "q": "",
            "admission": "",
            "roll": "",
            "classroom_id": "",
            "section_id": "",
            "year": "",
            "gender": "",
            "status": "",
            "branch": "",
            "per_page": "20",
        }
        empty_stats = {
            "total": 0,
            "active": 0,
            "present_today": 0,
            "absent_today": 0,
            "new_admissions_month": 0,
            "withdrawn": 0,
            "boys": 0,
            "girls": 0,
            "other_gender": 0,
        }
        return render(
            request,
            "core/school/students_list.html",
            {
                "tenant_schema_error": err_msg,
                "students": Paginator([], 20).get_page(1),
                "classrooms": [],
                "sections": [],
                "years": [],
                "stats": empty_stats,
                "school": school,
                "filters_active": False,
                "filters": empty_filters,
            },
        )

    try:
        ClassRoom.objects.exists()
    except ProgrammingError:
        hint = tenant_migrate_cli_hint(school)
        tbl = ClassRoom._meta.db_table
        err_msg = (
            f"Required table “{tbl}” is missing in this school's database schema. "
            "That usually means tenant migrations were not applied for this school. "
            f"Run the following on the server, then reload this page:\n\n{hint}"
        )
        empty_filters = {
            "q": "",
            "admission": "",
            "roll": "",
            "classroom_id": "",
            "section_id": "",
            "year": "",
            "gender": "",
            "status": "",
            "branch": "",
            "per_page": "20",
        }
        empty_stats = {
            "total": 0,
            "active": 0,
            "present_today": 0,
            "absent_today": 0,
            "new_admissions_month": 0,
            "withdrawn": 0,
            "boys": 0,
            "girls": 0,
            "other_gender": 0,
        }
        return render(
            request,
            "core/school/students_list.html",
            {
                "tenant_schema_error": err_msg,
                "students": Paginator([], 20).get_page(1),
                "classrooms": [],
                "sections": [],
                "years": [],
                "stats": empty_stats,
                "school": school,
                "filters_active": False,
                "filters": empty_filters,
            },
        )

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
        order_by = ("classroom__grade_order", "classroom__name", "section__name", "roll_number")
    elif classroom_source == "section":
        order_by = ("section__classroom__grade_order", "section__classroom__name", "section__name", "roll_number")
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

    try:
        classrooms = list(ClassRoom.objects.select_related("academic_year").order_by(*ORDER_AY_START_GRADE_NAME))
        sections = list(Section.objects.all().order_by("name"))
        years = list(AcademicYear.objects.order_by("-start_date"))
    except ProgrammingError:
        hint = tenant_migrate_cli_hint(school)
        err_msg = (
            "Required tables are missing in this school's database schema. "
            "That usually means tenant migrations were not applied for this school. "
            f"Run the following on the server, then reload this page:\n\n{hint}"
        )
        return render(
            request,
            "core/school/students_list.html",
            {
                "tenant_schema_error": err_msg,
                "students": students,
                "classrooms": [],
                "sections": [],
                "years": [],
                "stats": stats,
                "school": school,
                "filters_active": False,
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
            },
        )

    filters_active = any(
        [
            q,
            admission,
            roll,
            classroom_id,
            section_id,
            academic_year_id,
            gender,
            status,
            branch,
        ]
    )

    return render(request, "core/school/students_list.html", {
        "students": students,
        "classrooms": classrooms,
        "sections": sections,
        "years": years,
        "stats": stats,
        "school": school,
        "filters_active": filters_active,
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


@transaction.non_atomic_requests
@admin_required
def school_student_add(request):
    """Add new student. Username = Admission Number (auto-generated if empty)."""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from apps.core.tenant_schema_repair import (
        recover_db_connection_for_request,
        run_migrate_schemas_for_tenant_school,
        tenant_schema_repair_should_retry,
    )

    def build_response():
        from .forms import StudentAddForm
        from apps.school_data.models import StudentDocument

        ensure_tenant_for_request(request)
        try:
            if getattr(connection, "needs_rollback", False):
                connection.rollback()
        except Exception:
            pass
        # Re-bind tenant after rollback / connection churn so form and template ORM hit the school schema.
        connection.set_tenant(school)

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
                    address=(
                        "\n".join([data.get("address_line1") or "", data.get("address_line2") or ""]).strip() or None
                    ),
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
                        "previous_school_academic_year": data.get("previous_school_academic_year") or "",
                        "previous_grade_completed": data.get("previous_grade_completed") or "",
                        "previous_board": data.get("previous_board") or "",
                        "previous_marks": data.get("previous_marks") or "",
                        "previous_marks_breakdown": data.get("previous_marks_breakdown") or "",
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
                    from apps.core.branding import get_platform_product_name

                    _brand = get_platform_product_name()
                    subject = f"Your {_brand} Login Credentials"
                    body = (
                        f"Hello {first_name},\n\n"
                        f"Your student account has been created.\n\n"
                        f"Username: Your Admission Number ({admission_number})\n"
                        f"Password: {password}\n\n"
                        "Please change your password after first login.\n\n"
                        f"— {_brand}"
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

    for attempt in (1, 2, 3):
        try:
            return build_response()
        except Exception as e:
            if attempt == 3 or not tenant_schema_repair_should_retry(e):
                raise
            run_migrate_schemas_for_tenant_school(school)
            recover_db_connection_for_request(request)


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

    student_profile_attendance_url = ""
    if has_feature_access(school, "attendance", user=request.user):
        student_profile_attendance_url = (
            reverse("core:attendance_list") + "?" + urlencode({"student_id": student.id})
        )
    student_profile_exams_url = ""
    if has_feature_access(school, "exams", user=request.user):
        student_profile_exams_url = reverse("core:school_exams_list")

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
        "student_profile_attendance_url": student_profile_attendance_url,
        "student_profile_exams_url": student_profile_exams_url,
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
        "previous_school_academic_year": academic.get("previous_school_academic_year") or "",
        "previous_grade_completed": academic.get("previous_grade_completed") or "",
        "previous_board": academic.get("previous_board") or "",
        "previous_marks": academic.get("previous_marks") or "",
        "previous_marks_breakdown": academic.get("previous_marks_breakdown") or "",
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
                    "previous_school_academic_year": data.get("previous_school_academic_year") or "",
                    "previous_grade_completed": data.get("previous_grade_completed") or "",
                    "previous_board": data.get("previous_board") or "",
                    "previous_marks": data.get("previous_marks") or "",
                    "previous_marks_breakdown": data.get("previous_marks_breakdown") or "",
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
@require_POST
def school_student_set_active(request, student_id: int):
    """AJAX/POST: toggle student login access (User.is_active)."""
    school = request.user.school
    if not school:
        return JsonResponse({"error": "Unauthorized."}, status=403)
    student = get_object_or_404(Student.objects.select_related("user"), id=student_id)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        payload = {}
    is_active = payload.get("is_active")
    if isinstance(is_active, str):
        is_active = is_active.strip().lower() in ("1", "true", "yes", "active", "on")
    if not isinstance(is_active, bool):
        return JsonResponse({"error": "Invalid is_active."}, status=400)

    reason = (payload.get("reason") or "").strip()
    relieved_date = (payload.get("relieved_date") or "").strip()

    student.user.is_active = is_active
    student.user.save(update_fields=["is_active"])

    extra = student.extra_data or {}
    status_block = extra.get("status") or {}
    status_block["record_status"] = "ACTIVE" if is_active else "INACTIVE"
    if not is_active:
        if reason:
            status_block["reason_for_deactivation"] = reason
        if relieved_date:
            status_block["relieved_date"] = relieved_date
    extra["status"] = status_block
    student.extra_data = extra
    student.save_with_audit(request.user)

    return JsonResponse({"ok": True, "is_active": is_active})


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

@transaction.non_atomic_requests
@admin_required
@feature_required("teachers")
def school_teachers_list(request):
    """List teachers with actions."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from apps.core.tenant_schema_repair import (
        recover_db_connection_for_request,
        run_migrate_schemas_for_tenant_school,
        tenant_schema_repair_should_retry,
    )

    def build_response():
        from django.utils import timezone
        from django.db.models import Q
        from apps.school_data.models import Subject, ClassRoom

        ensure_tenant_for_request(request)
        try:
            if getattr(connection, "needs_rollback", False):
                connection.rollback()
        except Exception:
            pass

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
            "subjects": list(Subject.objects.order_by("display_order", "name")),
            "classrooms": list(
                ClassRoom.objects.select_related("academic_year").order_by(*ORDER_AY_START_GRADE_NAME)
            ),
        }

        filters = {
            "q": q,
            "employee_id": employee_id,
            "subject": subject_id,
            "classroom": class_id,
            "status": status,
        }
        has_active_filters = bool(
            q or employee_id or subject_id or class_id or status
        )

        view_mode = (request.GET.get("view") or "list").strip().lower()
        if view_mode not in ("list", "card"):
            view_mode = "list"
        params = request.GET.copy()
        params["view"] = "list"
        teachers_view_list_url = f"{request.path}?{params.urlencode()}"
        params["view"] = "card"
        teachers_view_card_url = f"{request.path}?{params.urlencode()}"

        # Force ORM evaluation before render so missing tenant tables hit migrate+retry here.
        teachers_evaluated = list(teachers)

        return render(
            request,
            "core/school/teachers_list.html",
            {
                "teachers": teachers_evaluated,
                "stats": stats,
                "filters": filters,
                "filter_options": filter_options,
                "has_active_filters": has_active_filters,
                "view_mode": view_mode,
                "teachers_view_list_url": teachers_view_list_url,
                "teachers_view_card_url": teachers_view_card_url,
            },
        )

    for attempt in (1, 2):
        try:
            return build_response()
        except Exception as e:
            if attempt == 2 or not tenant_schema_repair_should_retry(e):
                raise
            # If the first query failed, the connection may be in an aborted transaction
            # state. Recover first so migration commands don't run on a broken session.
            # Don't close the connection while Django is still unwinding the original
            # DB exception; that can produce a secondary "connection already closed".
            recover_db_connection_for_request(request, close_connection=False)
            run_migrate_schemas_for_tenant_school(school)
            recover_db_connection_for_request(request)


@transaction.non_atomic_requests
@admin_required
def school_teacher_add(request):
    """Add new teacher (extended profile, same structure as student master)."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from apps.core.tenant_schema_repair import (
        recover_db_connection_for_request,
        run_migrate_schemas_for_tenant_school,
        tenant_schema_repair_should_retry,
    )

    def build_response():
        from .teacher_forms import TeacherMasterForm
        from apps.school_data.models import TeacherClassSection

        ensure_tenant_for_request(request)
        try:
            if getattr(connection, "needs_rollback", False):
                connection.rollback()
        except Exception:
            pass

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

                # Optional: assign teacher to particular sections within selected classes.
                raw_pairs = request.POST.getlist("class_sections") or []
                valid_pairs = set()
                class_ids = set(teacher.classrooms.values_list("id", flat=True))
                if raw_pairs and class_ids:
                    # Preload sections per classroom to validate fast.
                    classroom_section_map = {
                        c.id: set(c.sections.values_list("id", flat=True))
                        for c in ClassRoom.objects.filter(id__in=class_ids).prefetch_related("sections")
                    }
                    for p in raw_pairs:
                        try:
                            cls_id_str, sec_id_str = (p or "").split(":", 1)
                            cls_id = int(cls_id_str)
                            sec_id = int(sec_id_str)
                        except Exception:
                            continue
                        if cls_id in class_ids and sec_id in classroom_section_map.get(cls_id, set()):
                            valid_pairs.add((cls_id, sec_id))

                if valid_pairs:
                    TeacherClassSection.objects.bulk_create(
                        [
                            TeacherClassSection(teacher=teacher, classroom_id=cls_id, section_id=sec_id)
                            for (cls_id, sec_id) in sorted(valid_pairs)
                        ],
                        ignore_conflicts=True,
                    )
            messages.success(request, "Teacher created.")
            return redirect("core:school_teacher_view", teacher_id=teacher.id)
        classrooms_with_sections = list(
            ClassRoom.objects.select_related("academic_year")
            .prefetch_related("sections")
            .order_by(*ORDER_AY_START_GRADE_NAME)
        )
        return render(
            request,
            "core/school/teacher_master_form.html",
            {
                "form": form,
                "teacher": None,
                "classrooms_with_sections": classrooms_with_sections,
                "assigned_class_section_pairs": set(),
            },
        )

    for attempt in (1, 2):
        try:
            return build_response()
        except Exception as e:
            if attempt == 2 or not tenant_schema_repair_should_retry(e):
                raise
            recover_db_connection_for_request(request, close_connection=False)
            run_migrate_schemas_for_tenant_school(school)
            recover_db_connection_for_request(request)


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


@transaction.non_atomic_requests
@admin_required
def school_teacher_edit(request, teacher_id):
    """Edit teacher extended profile."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from apps.core.tenant_schema_repair import (
        recover_db_connection_for_request,
        run_migrate_schemas_for_tenant_school,
        tenant_schema_repair_should_retry,
    )

    def build_response():
        from .teacher_forms import TeacherMasterForm
        from apps.school_data.models import TeacherClassSection

        ensure_tenant_for_request(request)
        try:
            if getattr(connection, "needs_rollback", False):
                connection.rollback()
        except Exception:
            pass
        teacher = get_object_or_404(Teacher, id=teacher_id)
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
                teacher.user.username = (data.get("username") or "").strip()
                # Security: teachers must never be able to become other portal roles from this screen.
                teacher.user.role = User.Roles.TEACHER
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

                # Assign teacher to particular sections within selected classes.
                raw_pairs = request.POST.getlist("class_sections") or []
                requested_pairs = set()
                class_ids = set(teacher.classrooms.values_list("id", flat=True))
                if raw_pairs and class_ids:
                    classroom_section_map = {
                        c.id: set(c.sections.values_list("id", flat=True))
                        for c in ClassRoom.objects.filter(id__in=class_ids).prefetch_related("sections")
                    }
                    for p in raw_pairs:
                        try:
                            cls_id_str, sec_id_str = (p or "").split(":", 1)
                            cls_id = int(cls_id_str)
                            sec_id = int(sec_id_str)
                        except Exception:
                            continue
                        if cls_id in class_ids and sec_id in classroom_section_map.get(cls_id, set()):
                            requested_pairs.add((cls_id, sec_id))

                # Replace assignments for currently selected classrooms (and remove any stale ones).
                TeacherClassSection.objects.filter(teacher=teacher).exclude(classroom_id__in=class_ids).delete()
                TeacherClassSection.objects.filter(teacher=teacher, classroom_id__in=class_ids).delete()
                if requested_pairs:
                    TeacherClassSection.objects.bulk_create(
                        [
                            TeacherClassSection(teacher=teacher, classroom_id=cls_id, section_id=sec_id)
                            for (cls_id, sec_id) in sorted(requested_pairs)
                        ],
                        ignore_conflicts=True,
                    )
            messages.success(request, "Teacher updated.")
            return redirect("core:school_teacher_view", teacher_id=teacher.id)

        classrooms_with_sections = list(
            ClassRoom.objects.select_related("academic_year")
            .prefetch_related("sections")
            .order_by(*ORDER_AY_START_GRADE_NAME)
        )
        assigned_pairs = set(
            TeacherClassSection.objects.filter(teacher=teacher).values_list("classroom_id", "section_id")
        )
        assigned_class_section_pairs = {f"{c}:{s}" for (c, s) in assigned_pairs}
        return render(
            request,
            "core/school/teacher_master_form.html",
            {
                "form": form,
                "teacher": teacher,
                "classrooms_with_sections": classrooms_with_sections,
                "assigned_class_section_pairs": assigned_class_section_pairs,
            },
        )

    for attempt in (1, 2):
        try:
            return build_response()
        except Exception as e:
            if attempt == 2 or not tenant_schema_repair_should_retry(e):
                raise
            recover_db_connection_for_request(request, close_connection=False)
            run_migrate_schemas_for_tenant_school(school)
            recover_db_connection_for_request(request)


@admin_required
@require_POST
def school_teacher_set_active(request, teacher_id: int):
    """AJAX/POST: toggle teacher login access (User.is_active)."""
    school = request.user.school
    if not school:
        return JsonResponse({"error": "Unauthorized."}, status=403)
    teacher = get_object_or_404(Teacher.objects.select_related("user"), id=teacher_id)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        payload = {}
    is_active = payload.get("is_active")
    if isinstance(is_active, str):
        is_active = is_active.strip().lower() in ("1", "true", "yes", "active", "on")
    if not isinstance(is_active, bool):
        return JsonResponse({"error": "Invalid is_active."}, status=400)

    reason = (payload.get("reason") or "").strip()
    relieved_date = (payload.get("relieved_date") or "").strip()  # YYYY-MM-DD (optional)

    teacher.user.is_active = is_active
    teacher.user.save(update_fields=["is_active"])

    # Mirror into teacher.extra_data.status for HR/audit UI (no schema change).
    extra = teacher.extra_data or {}
    status_block = extra.get("status") or {}
    status_block["record_status"] = "ACTIVE" if is_active else "INACTIVE"
    if not is_active:
        if reason:
            status_block["reason_for_deactivation"] = reason
        if relieved_date:
            status_block["relieved_date"] = relieved_date
    extra["status"] = status_block
    teacher.extra_data = extra
    teacher.save_with_audit(request.user)

    return JsonResponse({"ok": True, "is_active": is_active})


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
    import logging

    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from django.db import ProgrammingError, InterfaceError, InternalError
    from django.db import transaction
    from django.core.paginator import Paginator
    from apps.core.db_schema_utils import missing_tables
    from apps.core.tenant_schema_repair import recover_db_connection_for_request

    logger = logging.getLogger(__name__)

    def safe_fallback(search: str):
        paginator = Paginator([], 15)
        sections = paginator.get_page(1)
        return render(request, "core/school/sections.html", {
            "sections": sections,
            "total_sections": 0,
            "filters": {"q": search},
            "stats": None,
        })

    def build_response():
        ensure_tenant_for_request(request)
        # Ensure we start clean; rollback must run on the tenant DB alias connection.
        recover_db_connection_for_request(request, close_connection=False)
        # Make sure the DB connection is open before we do any ORM work.
        from django.db import connection as default_connection
        try:
            default_connection.ensure_connection()
        except Exception:
            # If we can't connect, return a safe empty page instead of crashing.
            return safe_fallback(request.GET.get("q", "").strip())

        schema = getattr(school, "schema_name", "") or ""
        miss = missing_tables(
            schema,
            (
                "school_data_section",
                "school_data_student",
                "school_data_classroom",
            ),
        )
        if miss:
            logger.error(
                "Tenant schema missing required tables; returning empty sections list. schema=%s missing=%s path=%s",
                schema,
                ",".join(miss),
                request.path,
            )
            return safe_fallback(request.GET.get("q", "").strip())

        qs = Section.objects.prefetch_related("classrooms").annotate(student_count=Count("students")).order_by("name")
        total_sections = Section.objects.count()
        search = request.GET.get("q", "").strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))

        if request.GET.get("export") == "csv":
            resp = HttpResponse(content_type="text/csv")
            resp["Content-Disposition"] = 'attachment; filename="sections.csv"'
            w = csv.writer(resp)
            w.writerow(["Section Name", "Description", "Classes", "Total Students"])
            for sec in qs:
                classes = ", ".join(sec.classrooms.all().values_list("name", flat=True))
                w.writerow([
                    sec.name,
                    (sec.description or "").replace("\n", " ").strip(),
                    classes,
                    sec.student_count,
                ])
            return resp

        paginator = Paginator(qs, 15)
        page = request.GET.get("page", 1)
        sections = paginator.get_page(page)
        # Force evaluation inside the try/except so template rendering won't trigger
        # additional DB work after a connection reset/close.
        try:
            sections.object_list = list(sections.object_list)
        except Exception:
            # If the DB fails here, fall back to an empty list.
            sections.object_list = []
        stats = None
        if total_sections > 0:
            stats = {
                "total_sections": total_sections,
                "total_students": Student.objects.count(),
                "linked_classes": ClassRoom.objects.annotate(n=Count("sections")).filter(n__gt=0).count(),
            }
        return render(request, "core/school/sections.html", {
            "sections": sections,
            "total_sections": total_sections,
            "filters": {"q": search},
            "stats": stats,
        })

    # Safe query handling: if ORM evaluation still fails (race, drift), rollback and return fallback.
    try:
        return build_response()
    except (ProgrammingError, InterfaceError, InternalError) as e:
        schema = getattr(school, "schema_name", "") or ""
        logger.exception("DB error in school_sections; returning fallback. schema=%s path=%s", schema, request.path)
        try:
            if transaction.get_connection().in_atomic_block:
                transaction.set_rollback(True)
        except Exception:
            pass
        try:
            recover_db_connection_for_request(request)
        except Exception:
            pass
        return safe_fallback(request.GET.get("q", "").strip())


@admin_required
def school_section_add(request):
    """Add new section form page."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from .forms import SectionForm
    from apps.core.tenant_schema_repair import (
        recover_db_connection_for_request,
        run_migrate_schemas_for_tenant_school,
        tenant_schema_repair_should_retry,
    )

    def build_response():
        ensure_tenant_for_request(request)
        recover_db_connection_for_request(request, close_connection=False)
        form = SectionForm(school, request.POST or None)
        if request.method == "POST" and form.is_valid():
            section = form.save(commit=False)
            section.save_with_audit(request.user)
            return redirect("core:school_sections")
        return render(request, "core/school/section_add.html", {"form": form, "title": "Add Section"})

    for attempt in (1, 2):
        try:
            return build_response()
        except Exception as e:
            if attempt == 2 or not tenant_schema_repair_should_retry(e):
                raise
            run_migrate_schemas_for_tenant_school(school)
            recover_db_connection_for_request(request)


@admin_required
def school_section_edit(request, section_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from apps.core.tenant_schema_repair import (
        recover_db_connection_for_request,
        run_migrate_schemas_for_tenant_school,
        tenant_schema_repair_should_retry,
    )

    def build_response():
        ensure_tenant_for_request(request)
        recover_db_connection_for_request(request, close_connection=False)
        section = get_object_or_404(Section, id=section_id)
        from .forms import SectionForm
        form = SectionForm(school, request.POST or None, instance=section)
        if request.method == "POST" and form.is_valid():
            obj = form.save(commit=False)
            obj.modified_by = request.user
            obj.save()
            return redirect("core:school_sections")
        return render(request, "core/school/section_edit.html", {"form": form, "section": section})

    for attempt in (1, 2):
        try:
            return build_response()
        except Exception as e:
            if attempt == 2 or not tenant_schema_repair_should_retry(e):
                raise
            run_migrate_schemas_for_tenant_school(school)
            recover_db_connection_for_request(request)


@admin_required
def school_section_delete(request, section_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from apps.core.tenant_schema_repair import (
        recover_db_connection_for_request,
        run_migrate_schemas_for_tenant_school,
        tenant_schema_repair_should_retry,
    )

    def build_response():
        ensure_tenant_for_request(request)
        recover_db_connection_for_request(request, close_connection=False)
        section = get_object_or_404(Section, id=section_id)
        if request.method != "POST":
            return redirect("core:school_sections")
        if section.students.exists():
            return redirect("core:school_sections")
        section.delete()
        return redirect("core:school_sections")

    for attempt in (1, 2):
        try:
            return build_response()
        except Exception as e:
            if attempt == 2 or not tenant_schema_repair_should_retry(e):
                raise
            run_migrate_schemas_for_tenant_school(school)
            recover_db_connection_for_request(request)


# ======================
# School Admin: Academic Years
# ======================

@transaction.non_atomic_requests
@admin_required
def school_academic_years(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from apps.core.tenant_schema_repair import (
        recover_db_connection_for_request,
        run_migrate_schemas_for_tenant_school,
        tenant_schema_repair_should_retry,
    )

    def build_response():
        from django.core.paginator import Paginator

        ensure_tenant_for_request(request)
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
        # Page keeps a sliced QuerySet until iteration; evaluate here so missing-table
        # errors surface inside the migrate+retry loop instead of during template render.
        _ol = academic_years.object_list
        if hasattr(_ol, "model"):
            academic_years.object_list = list(_ol)
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

    for attempt in (1, 2):
        try:
            return build_response()
        except Exception as e:
            if attempt == 2 or not tenant_schema_repair_should_retry(e):
                raise
            run_migrate_schemas_for_tenant_school(school)
            recover_db_connection_for_request(request)


@admin_required
def school_academic_year_add(request):
    """Dedicated create page (replaces modal POST on list, which was easy to break)."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from .academic_year_wizard import apply_wizard_after_year_created, sanitize_wizard, validate_wizard_payload
    from .forms import AcademicYearForm

    previous_years = list(AcademicYear.objects.order_by("-start_date"))
    from apps.school_data.classroom_ordering import grade_order_from_name

    class_names = sorted(
        {
            n
            for n in ClassRoom.objects.filter(academic_year__isnull=False).values_list("name", flat=True)
            if n
        },
        key=lambda x: (grade_order_from_name(x), x.lower()),
    )
    ay_weekday_choices = [
        (1, "Monday"),
        (2, "Tuesday"),
        (3, "Wednesday"),
        (4, "Thursday"),
        (5, "Friday"),
        (6, "Saturday"),
        (7, "Sunday"),
    ]

    if request.method == "POST":
        form = AcademicYearForm(request.POST)
        raw_wizard = request.POST.get("wizard_settings_json") or "{}"
        try:
            wizard_raw = json.loads(raw_wizard)
        except json.JSONDecodeError:
            wizard_raw = {}
        wizard_sanitized = sanitize_wizard(wizard_raw if isinstance(wizard_raw, dict) else {})

        if form.is_valid():
            v_err = validate_wizard_payload(
                name=form.cleaned_data["name"],
                start_date=form.cleaned_data["start_date"],
                end_date=form.cleaned_data["end_date"],
                wizard=wizard_sanitized,
                exclude_year_id=None,
            )
            if v_err:
                form.add_error(None, v_err)
            else:
                obj = form.save(commit=False)
                obj.wizard_settings = wizard_sanitized
                obj.save_with_audit(request.user)
                try:
                    extra_logs = apply_wizard_after_year_created(obj, request.user, wizard_sanitized)
                except Exception as exc:
                    messages.warning(
                        request,
                        f'Academic year "{obj.name}" was saved, but some optional setup steps failed: {exc}',
                    )
                else:
                    messages.success(
                        request,
                        f'Academic year "{obj.name}" was created successfully.',
                    )
                    for line in extra_logs[:6]:
                        messages.info(request, line)
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
            "previous_years": previous_years,
            "promotion_class_names": class_names,
            "promotion_classes_json": json.dumps(class_names),
            "ay_weekday_choices": ay_weekday_choices,
        },
    )


@admin_required
def school_academic_year_set_active(request, year_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
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
    ensure_tenant_for_request(request)
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
            "previous_years": [],
            "promotion_class_names": [],
        },
    )


@admin_required
def school_academic_year_delete(request, year_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
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
    for c in ClassRoom.objects.filter(academic_year=target_year).order_by(*ORDER_GRADE_NAME):
        if _extract_grade_num(c.name) == target_no:
            return c
    return None


@admin_required
@feature_required("students")
def school_promote_students(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
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
        students = list(
            qs.order_by("classroom__grade_order", "classroom__name", "section__name", "roll_number")
        )
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
                        for c in ClassRoom.objects.filter(academic_year=to_year).order_by(*ORDER_GRADE_NAME):
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
    ensure_tenant_for_request(request)
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

@transaction.non_atomic_requests
@admin_required
@feature_required("students")
def school_classes(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    import logging
    from django.db import ProgrammingError, InterfaceError, InternalError
    from django.db import transaction
    from apps.core.db_schema_utils import missing_tables
    from apps.core.tenant_schema_repair import recover_db_connection_for_request

    logger = logging.getLogger(__name__)

    def build_response():
        from django.core.paginator import Paginator
        from django_tenants.utils import get_tenant_database_alias
        from django.db.models import Count, Q

        ensure_tenant_for_request(request)
        recover_db_connection_for_request(request, close_connection=False)
        alias = get_tenant_database_alias()
        try:
            # Ensure the tenant DB alias connection is open; queries below must run
            # on the tenant-bound connection (schema-per-tenant search_path).
            from django.db import connections

            connections[alias].ensure_connection()
        except Exception:
            pass

        # Prefer the schema bound on the active tenant connection (more reliable than School.schema_name
        # if user-school binding is partial or the object was loaded from public schema).
        try:
            from django.db import connections

            schema = getattr(connections[alias], "schema_name", None) or getattr(school, "schema_name", "") or ""
        except Exception:
            schema = getattr(school, "schema_name", "") or ""

        miss = missing_tables(schema, ("school_data_classroom",)) if schema else []
        if schema and miss:
            logger.error(
                "Missing classroom table; returning empty classes list. schema=%s path=%s",
                schema,
                request.path,
            )
            paginator = Paginator([], 15)
            classes = paginator.get_page(1)
            return render(request, "core/school/classes/list.html", {
                "classes": classes,
                "total_classes": 0,
                "academic_years": [],
                "filters": {"academic_year": request.GET.get("academic_year"), "q": request.GET.get("q", "").strip()},
            })
        if not schema:
            logger.warning(
                "school_classes called without schema_name; attempting query anyway. user_id=%s path=%s",
                getattr(request.user, "id", None),
                request.path,
            )

        qs = (
            ClassRoom.objects.all()
            .annotate(
                section_count=Count("sections", distinct=True),
                student_count=Count("enrollments", filter=Q(enrollments__is_current=True), distinct=True),
            )
            .order_by("grade_order", "name")
        )
        academic_year_id = request.GET.get("academic_year")
        if academic_year_id:
            qs = qs.filter(academic_year_id=academic_year_id)
        search = request.GET.get("q", "").strip()
        if search:
            qs = qs.filter(name__icontains=search)
        logger.info(
            "school_classes list user_id=%s schema=%s filters(academic_year=%s q=%s)",
            getattr(request.user, "id", None),
            schema,
            academic_year_id,
            search,
        )
        paginator = Paginator(qs, 15)
        page = request.GET.get("page", 1)
        classes = paginator.get_page(page)
        try:
            classes.object_list = list(classes.object_list)
        except Exception:
            classes.object_list = []

        # Academic year dropdown is optional; tolerate missing table by returning empty list.
        try:
            schema = getattr(school, "schema_name", "") or ""
            if missing_tables(schema, ("school_data_academicyear",)):
                academic_years = []
            else:
                academic_years = list(AcademicYear.objects.only("pk", "name", "start_date", "end_date", "is_active").order_by("-start_date"))
        except Exception:
            academic_years = []
        try:
            total_classes = ClassRoom.objects.count()
        except Exception:
            logger.exception(
                "school_classes count failed; using page length fallback. user_id=%s schema=%s path=%s",
                getattr(request.user, "id", None),
                schema,
                request.path,
            )
            total_classes = 0
        return render(request, "core/school/classes/list.html", {
            "classes": classes,
            "total_classes": total_classes or len(getattr(classes, "object_list", []) or []),
            "academic_years": academic_years,
            "filters": {"academic_year": academic_year_id, "q": search},
        })

    try:
        return build_response()
    except (ProgrammingError, InterfaceError, InternalError):
        schema = getattr(school, "schema_name", "") or ""
        logger.exception("DB error in school_classes; returning safe fallback. schema=%s path=%s", schema, request.path)
        try:
            if transaction.get_connection().in_atomic_block:
                transaction.set_rollback(True)
        except Exception:
            pass
        try:
            recover_db_connection_for_request(request)
        except Exception:
            pass
        from django.core.paginator import Paginator
        paginator = Paginator([], 15)
        classes = paginator.get_page(1)
        return render(request, "core/school/classes/list.html", {
            "classes": classes,
            "total_classes": 0,
            "academic_years": [],
            "filters": {"academic_year": request.GET.get("academic_year"), "q": request.GET.get("q", "").strip()},
        })


@transaction.non_atomic_requests
@admin_required
def school_class_add(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    import logging
    from django.contrib import messages
    from django.db import ProgrammingError, InterfaceError, InternalError, IntegrityError
    from apps.core.tenant_schema_repair import (
        recover_db_connection_for_request,
        run_migrate_schemas_for_tenant_school,
        tenant_schema_repair_should_retry,
    )
    logger = logging.getLogger(__name__)

    def build_response():
        from .forms import ClassRoomForm
        from apps.core.db_schema_utils import missing_tables
        # region agent log
        import json as _json
        import time as _time
        def _dbg(hypothesisId: str, message: str, data: dict):
            try:
                with open("debug-b9d4c6.log", "a", encoding="utf-8") as f:
                    f.write(_json.dumps({
                        "sessionId": "b9d4c6",
                        "runId": "classes_add_pre",
                        "hypothesisId": hypothesisId,
                        "location": "apps/core/views.py:school_class_add",
                        "message": message,
                        "data": data,
                        "timestamp": int(_time.time() * 1000),
                    }) + "\n")
            except Exception:
                pass
        # endregion agent log

        ensure_tenant_for_request(request)
        # Ensure clean tenant connection (rollback on correct alias).
        recover_db_connection_for_request(request, close_connection=False)
        try:
            # Ensure tenant alias connection is open and bound.
            from django.db import connections
            from django_tenants.utils import get_tenant_database_alias

            alias = get_tenant_database_alias()
            connections[alias].ensure_connection()
            try:
                connections[alias].set_tenant(school)
            except Exception:
                pass
        except Exception:
            pass

        # region agent log
        try:
            from django.db import connection as _c
            from django_tenants.utils import get_tenant_database_alias as _g
            _alias = _g()
            _dbg("H1", "after_bind", {
                "path": request.path,
                "method": request.method,
                "user_id": getattr(request.user, "id", None),
                "school_id": getattr(school, "id", None),
                "school_schema": getattr(school, "schema_name", None),
                "conn_schema": getattr(_c, "schema_name", None),
                "tenant_alias": _alias,
            })
        except Exception:
            pass
        # endregion agent log

        # If tenant tables are genuinely missing for this school schema, show a clear message
        # and render safely (no template-time DB queries). This probe uses information_schema
        # so it is correct even if search_path/tenant binding is wrong.
        schema = getattr(school, "schema_name", "") or ""
        try:
            miss = (
                missing_tables(
                    schema,
                    ("school_data_academicyear", "school_data_section", "school_data_classroom"),
                )
                if schema
                else []
            )
        except Exception as probe_exc:
            miss = ["__probe_error__"]
            _dbg("H3", "missing_tables_exception", {"schema": schema, "err": str(probe_exc)[:200]})

        _dbg("H3", "missing_tables_result", {"schema": schema, "miss": miss})
        if miss:
            # missing_tables() uses information_schema, but it returns False on probe errors too.
            # Confirm with a real ORM probe before telling the user to run migrations.
            try:
                from django_tenants.utils import get_tenant_database_alias
                from apps.school_data.models import AcademicYear

                alias = get_tenant_database_alias()
                list(AcademicYear.objects.using(alias).only("pk")[:1])
                miss = []
                _dbg("H3", "orm_probe_ok", {"alias": alias, "schema": schema})
            except Exception:
                # If even the ORM probe fails, treat as genuinely not ready / not bound.
                _dbg("H3", "orm_probe_failed", {"schema": schema})
                pass

        if miss:
            hint = tenant_migrate_cli_hint(school)
            messages.error(
                request,
                "Database tables for this school are not ready yet. "
                f"Run tenant migrations and reload this page:\n\n{hint}",
            )
            form = ClassRoomForm(school, request.POST or None)
            try:
                from apps.school_data.models import AcademicYear, Section

                form.fields["academic_year"].queryset = AcademicYear.objects.none()
                form.fields["sections"].queryset = Section.objects.none()
            except Exception:
                pass
            _dbg("H1", "rendering_with_empty_pickes_due_to_miss", {"schema": schema, "miss": miss})
            return render(request, "core/school/classes/form.html", {"form": form, "title": "Add Class"})

        # Tables exist: proceed normally. If we still hit a ProgrammingError, it's almost
        # always a tenant binding/search_path issue, not a migration issue.
        form = ClassRoomForm(school, request.POST or None)
        # region agent log
        try:
            ay_qs = getattr(form.fields.get("academic_year"), "queryset", None)
            sec_qs = getattr(form.fields.get("sections"), "queryset", None)
            _dbg("H4", "form_querysets", {
                "ay_db": getattr(ay_qs, "db", None),
                "ay_count": ay_qs.count() if ay_qs is not None else None,
                "sec_db": getattr(sec_qs, "db", None),
                "sec_count": sec_qs.count() if sec_qs is not None else None,
            })
        except Exception:
            pass
        # endregion agent log

        # Evaluate DB-backed field querysets here so tenant/schema issues are handled
        # inside the view (retry loop) instead of crashing during template rendering.
        try:
            ay_qs = getattr(form.fields.get("academic_year"), "queryset", None)
            if ay_qs is not None:
                list(ay_qs.only("pk")[:1])
            sec_qs = getattr(form.fields.get("sections"), "queryset", None)
            if sec_qs is not None:
                list(sec_qs.only("pk")[:1])
        except (ProgrammingError, InterfaceError, InternalError):
            schema = getattr(school, "schema_name", "") or ""
            logger.exception(
                "DB error building ClassRoomForm pickers. schema=%s path=%s",
                schema,
                request.path,
            )
            try:
                messages.error(request, "Database connection was reset for this school. Please reload the page and try again.")
            except Exception:
                pass
            # Rebind tenant and let the outer retry loop handle it.
            raise
        if request.method == "POST":
            schema = getattr(school, "schema_name", "") or ""
            try:
                payload = request.POST.dict()
                payload.pop("csrfmiddlewaretoken", None)
            except Exception:
                payload = {}
            logger.info(
                "school_class_add POST user_id=%s username=%s schema=%s path=%s payload_keys=%s",
                getattr(request.user, "id", None),
                getattr(request.user, "username", ""),
                schema,
                request.path,
                sorted(list(payload.keys())) if isinstance(payload, dict) else [],
            )

            if form.is_valid():
                try:
                    obj = form.save(commit=False)
                    obj.save_with_audit(request.user)
                    form.save_m2m()
                except IntegrityError:
                    logger.exception(
                        "IntegrityError creating ClassRoom. user_id=%s schema=%s path=%s",
                        getattr(request.user, "id", None),
                        schema,
                        request.path,
                    )
                    messages.error(
                        request,
                        "Could not create class due to a database constraint (it may already exist). Please review the fields and try again.",
                    )
                except Exception:
                    logger.exception(
                        "Unexpected error creating ClassRoom. user_id=%s schema=%s path=%s",
                        getattr(request.user, "id", None),
                        schema,
                        request.path,
                    )
                    messages.error(request, "Something went wrong while creating the class. Please try again.")
                else:
                    messages.success(request, f"Class created: {obj.name}")
                    return redirect("core:school_classes")
            else:
                logger.warning(
                    "school_class_add invalid form user_id=%s schema=%s path=%s errors=%s ay_choices=%s section_choices=%s",
                    getattr(request.user, "id", None),
                    schema,
                    request.path,
                    form.errors.as_json() if hasattr(form, "errors") else "",
                    len(getattr(form.fields.get("academic_year"), "choices", []) or []),
                    len(getattr(form.fields.get("sections"), "choices", []) or []),
                )
                messages.error(request, "Please fix the highlighted fields and try again.")
        # region agent log
        try:
            # Do NOT consume messages here (iterating messages.get_messages() would clear them).
            _msgs = []
            try:
                storage = getattr(request, "_messages", None)
                queued = list(getattr(storage, "_queued_messages", []) or []) if storage is not None else []
                _msgs = [{"level": getattr(m, "level", None), "tags": getattr(m, "tags", ""), "msg": str(m)[:200]} for m in queued]
            except Exception:
                _msgs = ["<unavailable>"]
            ay_qs = getattr(form.fields.get("academic_year"), "queryset", None)
            sec_qs = getattr(form.fields.get("sections"), "queryset", None)
            ay_html = str(form["academic_year"]) if "academic_year" in form.fields else ""
            opt_count = ay_html.count("<option")
            _dbg("H2", "before_render", {
                "method": request.method,
                "conn_schema": getattr(connection, "schema_name", None),
                "school_schema": getattr(school, "schema_name", None),
                "ay_count": ay_qs.count() if ay_qs is not None else None,
                "sec_count": sec_qs.count() if sec_qs is not None else None,
                "ay_option_tags": opt_count,
                "queued_messages": _msgs,
            })
        except Exception:
            pass
        # endregion agent log
        return render(
            request,
            "core/school/classes/form.html",
            {
                "form": form,
                "title": "Add Class",
                "dbg": (request.GET.get("dbg") == "1"),
                "dbg_ay_count": getattr(getattr(form.fields.get("academic_year"), "queryset", None), "count", lambda: None)(),
                "dbg_sec_count": getattr(getattr(form.fields.get("sections"), "queryset", None), "count", lambda: None)(),
                "dbg_ay_option_tags": (str(form["academic_year"]).count("<option") if "academic_year" in form.fields else None),
            },
        )

    for attempt in (1, 2):
        try:
            return build_response()
        except Exception as e:
            if isinstance(e, (ProgrammingError, InterfaceError, InternalError)):
                schema = getattr(school, "schema_name", "") or ""
                logger.exception("DB error in school_class_add; showing form fallback. schema=%s path=%s", schema, request.path)
                try:
                    from .utils import tenant_migrate_cli_hint

                    hint = tenant_migrate_cli_hint(school)
                    messages.error(
                        request,
                        "Database tables for this school are not ready yet. "
                        f"Run tenant migrations and reload this page:\n\n{hint}",
                    )
                except Exception:
                    pass
                try:
                    recover_db_connection_for_request(request)
                except Exception:
                    pass
                # Do not instantiate ClassRoomForm here: it may query tenant tables (AcademicYear/Section)
                # and crash again if migrations haven't been applied. Render a safe form with empty pickers.
                try:
                    from apps.school_data.models import AcademicYear, Section
                    from .forms import ClassRoomForm

                    form = ClassRoomForm(school, request.POST or None)
                    try:
                        form.fields["academic_year"].queryset = AcademicYear.objects.none()
                    except Exception:
                        pass
                    try:
                        form.fields["sections"].queryset = Section.objects.none()
                    except Exception:
                        pass
                except Exception:
                    # Last-resort: render without a form instance.
                    form = None
                return render(request, "core/school/classes/form.html", {"form": form, "title": "Add Class"})
            if attempt == 2 or not tenant_schema_repair_should_retry(e):
                raise
            run_migrate_schemas_for_tenant_school(school)
            recover_db_connection_for_request(request)


@transaction.non_atomic_requests
@admin_required
def school_class_edit(request, class_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from apps.core.tenant_schema_repair import (
        recover_db_connection_for_request,
        run_migrate_schemas_for_tenant_school,
        tenant_schema_repair_should_retry,
    )

    def build_response():
        from .forms import ClassRoomForm
        from apps.school_data.models import TeacherClassSection

        ensure_tenant_for_request(request)
        try:
            if getattr(connection, "needs_rollback", False):
                connection.rollback()
        except Exception:
            pass
        classroom = get_object_or_404(ClassRoom, id=class_id)
        form = ClassRoomForm(school, request.POST or None, instance=classroom)
        if request.method == "POST" and form.is_valid():
            with transaction.atomic():
                obj = form.save(commit=False)
                obj.modified_by = request.user
                obj.save()
                form.save_m2m()

                selected_sections = list(obj.sections.all())
                teachers = {t.id: t for t in Teacher.objects.select_related("user").all()}
                for sec in selected_sections:
                    key = f"homeroom_teacher_{sec.id}"
                    raw = (request.POST.get(key) or "").strip()
                    teacher = teachers.get(int(raw)) if raw.isdigit() else None
                    ClassSectionTeacher.objects.update_or_create(
                        class_obj=obj,
                        section=sec,
                        defaults={"teacher": teacher},
                    )

                    TeacherClassSection.objects.filter(classroom=obj, section=sec).exclude(teacher=teacher).delete()
                    if teacher:
                        TeacherClassSection.objects.update_or_create(
                            teacher=teacher,
                            classroom=obj,
                            section=sec,
                            defaults={},
                        )
            return redirect("core:school_classes")
        existing_map = {
            a.section_id: a.teacher_id
            for a in ClassSectionTeacher.objects.filter(class_obj=classroom).only("section_id", "teacher_id")
        }
        teacher_choices = list(Teacher.objects.select_related("user").order_by("user__first_name", "user__username"))
        section_teacher_rows = []
        for sec in classroom.sections.all().order_by("name"):
            section_teacher_rows.append(
                {
                    "section": sec,
                    "teacher_id": existing_map.get(sec.id),
                }
            )
        return render(
            request,
            "core/school/classes/form.html",
            {
                "form": form,
                "classroom": classroom,
                "title": "Edit Class",
                "homeroom_teacher_choices": teacher_choices,
                "section_teacher_rows": section_teacher_rows,
            },
        )

    for attempt in (1, 2):
        try:
            return build_response()
        except Exception as e:
            if attempt == 2 or not tenant_schema_repair_should_retry(e):
                raise
            run_migrate_schemas_for_tenant_school(school)
            recover_db_connection_for_request(request)


@admin_required
def school_class_delete(request, class_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
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

@transaction.non_atomic_requests
@admin_required
@feature_required("students")
def school_subjects(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from apps.core.tenant_schema_repair import (
        recover_db_connection_for_request,
        run_migrate_schemas_for_tenant_school,
        tenant_schema_repair_should_retry,
    )

    def build_response():
        from django.core.paginator import Paginator

        ensure_tenant_for_request(request)
        try:
            if getattr(connection, "needs_rollback", False):
                connection.rollback()
        except Exception:
            pass

        qs = Subject.objects.all().order_by("display_order", "name")
        total_subjects = Subject.objects.count()
        search = request.GET.get("q", "").strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(code__icontains=search))
            filtered_count = qs.count()
            paginator = Paginator(qs, 15)
            page = request.GET.get("page", 1)
            subjects = paginator.get_page(page)
            _ol = subjects.object_list
            if hasattr(_ol, "model"):
                subjects.object_list = list(_ol)
            can_reorder = False
        else:
            filtered_count = total_subjects
            subjects = list(qs)
            can_reorder = total_subjects > 0
        subject_create_success = request.session.pop("subject_create_success", None)
        return render(request, "core/school/subjects/list.html", {
            "subjects": subjects,
            "total_subjects": total_subjects,
            "filtered_count": filtered_count,
            "filters": {"q": search},
            "can_reorder": can_reorder,
            "subject_create_success": subject_create_success,
        })

    for attempt in (1, 2):
        try:
            return build_response()
        except Exception as e:
            if attempt == 2 or not tenant_schema_repair_should_retry(e):
                raise
            run_migrate_schemas_for_tenant_school(school)
            recover_db_connection_for_request(request)


@admin_required
def school_subject_add(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    from .forms import SubjectForm
    form = SubjectForm(school, request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.save_with_audit(request.user)
        request.session["subject_create_success"] = {
            "name": obj.name,
            "code": (obj.code or "").strip(),
        }
        return redirect("core:school_subjects")
    return render(request, "core/school/subjects/form.html", {"form": form, "title": "Add Subject"})


@admin_required
def school_subject_edit(request, subject_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
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
@feature_required("students")
def school_subject_delete(request, subject_id):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    if request.method != "POST":
        return redirect("core:school_subjects")
    subject = get_object_or_404(Subject, id=subject_id)
    subject.delete()
    return redirect("core:school_subjects")


@admin_required
@feature_required("students")
@require_POST
def api_subjects_save_order(request):
    """Persist subject list order (display_order) for the school tenant."""
    if not request.user.school:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    try:
        payload = json.loads(request.body.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    order = payload.get("order")
    if not isinstance(order, list) or not order:
        return JsonResponse({"ok": False, "error": "order must be a non-empty list"}, status=400)
    try:
        ids = [int(x) for x in order]
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid subject ids"}, status=400)
    if len(ids) != len(set(ids)):
        return JsonResponse({"ok": False, "error": "Duplicate subject ids"}, status=400)

    total = Subject.objects.count()
    if total == 0:
        return JsonResponse({"ok": True, "message": "No subjects."})
    if len(ids) != total:
        return JsonResponse(
            {
                "ok": False,
                "error": "The order list must include every subject exactly once.",
            },
            status=400,
        )
    found = set(Subject.objects.filter(id__in=ids).values_list("id", flat=True))
    if len(found) != total:
        return JsonResponse({"ok": False, "error": "Unknown subject id."}, status=400)

    with transaction.atomic():
        for position, pk in enumerate(ids):
            Subject.objects.filter(pk=pk).update(display_order=(position + 1) * 10)
    return JsonResponse({"ok": True, "message": "Order saved."})


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
        ClassRoom.objects.prefetch_related("sections").order_by(*ORDER_AY_PK_GRADE_NAME)
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

    def _redirect_with_params(cid, sid, d_str, q_str, focus_sid=None, page=None, per_page=None):
        params = {"attendance_date": d_str}
        if cid:
            params["classroom_id"] = cid
        if sid:
            params["section_id"] = sid
        if q_str:
            params["q"] = q_str
        if focus_sid:
            params["student_id"] = focus_sid
        if page is not None and str(page).strip():
            params["page"] = page
        if per_page is not None and str(per_page).strip():
            params["per_page"] = per_page
        return redirect(reverse("core:attendance_list") + "?" + urlencode(params))

    def _parse_roll_per_page(raw):
        if raw is None:
            return "10"
        s = str(raw).strip().lower()
        if s == "all":
            return "all"
        try:
            n = int(s)
            if n in (10, 25, 50, 100):
                return str(n)
        except (TypeError, ValueError):
            pass
        return "10"

    def _roll_per_page_int(param):
        """Return page size for Paginator, or None for 'all'."""
        if param == "all":
            return None
        try:
            return int(param)
        except (TypeError, ValueError):
            return 10

    if request.method == "POST":
        classroom_id = _parse_int(request.POST.get("classroom_id"))
        section_id = _parse_int(request.POST.get("section_id"))
        date_str = request.POST.get("attendance_date", "").strip()
        search_q = request.POST.get("q", "").strip()
        post_focus_sid = _parse_int(request.POST.get("student_id"))
        post_page = (request.POST.get("page") or "1").strip()
        post_per_page = _parse_roll_per_page(request.POST.get("per_page"))

        wants_json = (
            request.headers.get("x-requested-with") == "XMLHttpRequest"
            or "application/json" in (request.headers.get("accept") or "")
        )

        if not classroom_id or not section_id or not date_str:
            msg = "Please select class, section, and date before saving."
            if wants_json:
                return JsonResponse({"ok": False, "message": "Failed to save attendance"}, status=400)
            messages.error(request, msg)
            return redirect("core:attendance_list")

        try:
            att_date = date.fromisoformat(date_str)
        except (ValueError, TypeError):
            if wants_json:
                return JsonResponse({"ok": False, "message": "Failed to save attendance"}, status=400)
            messages.error(request, "Invalid attendance date.")
            return redirect("core:attendance_list")

        from apps.school_data.calendar_policy import academic_year_for_date, resolve_day

        ay_att = academic_year_for_date(att_date)
        day_res = resolve_day(att_date, "student", ay=ay_att)
        if not day_res.is_working_day:
            msg = (
                f"{att_date.strftime('%d %b %Y')}: {day_res.label}. "
                "Attendance is not required on this day."
            )
            if wants_json:
                return JsonResponse({"ok": False, "message": msg}, status=400)
            messages.error(request, msg)
            return _redirect_with_params(
                classroom_id, section_id, date_str, search_q, post_focus_sid, post_page, post_per_page
            )

        if att_date > today:
            if wants_json:
                return JsonResponse({"ok": False, "message": "Failed to save attendance"}, status=400)
            messages.error(request, "Cannot mark attendance for a future date.")
            return _redirect_with_params(
                classroom_id, section_id, date_str, search_q, post_focus_sid, post_page, post_per_page
            )

        classroom = ClassRoom.objects.filter(pk=classroom_id).first()
        if not classroom or not classroom.sections.filter(pk=section_id).exists():
            if wants_json:
                return JsonResponse({"ok": False, "message": "Failed to save attendance"}, status=400)
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
            if wants_json:
                return JsonResponse({"ok": False, "message": "Failed to save attendance"}, status=500)
            messages.error(
                request,
                "Student list could not be loaded (database schema may be outdated). Run "
                f"{tenant_migrate_cli_hint(school)} then refresh.",
            )
            return _redirect_with_params(
                classroom_id, section_id, date_str, search_q, post_focus_sid, post_page, post_per_page
            )
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
            if wants_json:
                return JsonResponse({"ok": False, "message": "Failed to save attendance"}, status=400)
            messages.error(request, "No students match the current filters.")
            return _redirect_with_params(
                classroom_id, section_id, date_str, search_q, post_focus_sid, post_page, post_per_page
            )

        submitted_student_ids = []
        for key in request.POST:
            if not key.startswith("status_"):
                continue
            try:
                submitted_student_ids.append(int(key.replace("status_", "", 1)))
            except ValueError:
                continue
        if not submitted_student_ids:
            if wants_json:
                return JsonResponse({"ok": False, "message": "Failed to save attendance"}, status=400)
            messages.error(request, "No attendance rows to save.")
            return _redirect_with_params(
                classroom_id, section_id, date_str, search_q, post_focus_sid, post_page, post_per_page
            )

        students_by_id = {s.id: s for s in students}

        valid_status = set(Attendance.Status.values)
        active_ay = get_active_academic_year_obj()

        try:
            with transaction.atomic():
                for sid in submitted_student_ids:
                    student = students_by_id.get(sid)
                    if not student:
                        continue
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
                if wants_json:
                    return JsonResponse({"ok": False, "message": "Failed to save attendance"}, status=500)
                messages.error(
                    request,
                    "Attendance could not be saved: this school’s database schema is missing newer columns "
                    "(for example attendance.academic_year). From the project root, run: "
                    f"{tenant_migrate_cli_hint(school)}",
                )
            else:
                if wants_json:
                    return JsonResponse({"ok": False, "message": "Failed to save attendance"}, status=500)
                messages.error(
                    request,
                    "Attendance could not be saved due to a database error. If this persists, run "
                    f"{tenant_migrate_cli_hint(school)} and try again.",
                )
            return _redirect_with_params(
                classroom_id, section_id, date_str, search_q, post_focus_sid, post_page, post_per_page
            )
        except (InternalError, DatabaseError):
            # Do not call rollback() inside atomic(); outer handler after block exit.
            try:
                connection.rollback()
            except Exception:
                pass
            if wants_json:
                return JsonResponse({"ok": False, "message": "Failed to save attendance"}, status=500)
            messages.error(
                request,
                "Attendance could not be saved (database error—often a failed transaction or outdated schema). "
                f"Run {tenant_migrate_cli_hint(school)} then try again.",
            )
            return _redirect_with_params(
                classroom_id, section_id, date_str, search_q, post_focus_sid, post_page, post_per_page
            )

        try:
            marked_ids = set(
                Attendance.objects.filter(student__in=students, date=att_date).values_list(
                    "student_id", flat=True
                )
            )
        except (ProgrammingError, InternalError, DatabaseError):
            marked_ids = set()

        marked_total = len(marked_ids)
        pending_total = max(0, len(students) - marked_total)

        if wants_json:
            # Update attendance % for the students saved in this request (so UI can refresh without reload).
            pct_updates: dict[int, dict] = {}
            try:
                active_ay_for_pct = get_active_academic_year_obj()
                agg_qs = Attendance.objects.filter(student_id__in=submitted_student_ids)
                if active_ay_for_pct is not None:
                    agg_qs = agg_qs.filter(academic_year_id=active_ay_for_pct.id)
                agg = {
                    int(r["student_id"]): r
                    for r in agg_qs.values("student_id").annotate(
                        total_marked=Count("id"),
                        present_days=Count("id", filter=Q(status=Attendance.Status.PRESENT)),
                        absent_days=Count("id", filter=Q(status=Attendance.Status.ABSENT)),
                        leave_days=Count("id", filter=Q(status=Attendance.Status.LEAVE)),
                    )
                }
                for sid in submitted_student_ids:
                    row = agg.get(int(sid))
                    if not row or not row.get("total_marked"):
                        pct_updates[int(sid)] = {"att_pct": None, "att_pct_band": None, "att_tooltip": ""}
                        continue
                    present = int(row["present_days"])
                    total_m = int(row["total_marked"])
                    absent = int(row["absent_days"])
                    leave = int(row["leave_days"])
                    att_pct = round((present / total_m) * 100) if total_m else None
                    band = None
                    if att_pct is not None:
                        if att_pct >= 90:
                            band = "high"
                        elif att_pct >= 75:
                            band = "mid"
                        else:
                            band = "low"
                    tooltip = f"Present: {present} · Absent: {absent} · Leave: {leave} · Total marked: {total_m}"
                    pct_updates[int(sid)] = {
                        "att_pct": att_pct,
                        "att_pct_band": band,
                        "att_tooltip": tooltip,
                    }
            except Exception:
                pct_updates = {}
            return JsonResponse(
                {
                    "ok": True,
                    "message": "Attendance saved successfully",
                    "marked_count": len(submitted_student_ids),
                    "pending_count": pending_total,
                    "summary": {"marked": marked_total, "pending": pending_total},
                    "pct_updates": pct_updates,
                }
            )
        messages.success(
            request,
            f"Attendance saved for {len(submitted_student_ids)} student(s) on {att_date}.",
        )
        return _redirect_with_params(
            classroom_id, section_id, date_str, search_q, post_focus_sid, post_page, post_per_page
        )

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

    # Default view: pick the highest grade (by grade_order) and its first section.
    # This keeps /attendance/ immediately useful (loads a class roll call without extra clicks).
    if not focus_student and (not classroom_id or not section_id):
        if classrooms:
            default_class = max(classrooms, key=lambda c: (c.grade_order, c.id))
            default_sections = list(default_class.sections.all().order_by("name"))
            default_section = default_sections[0] if default_sections else None
            if default_class and default_section:
                return _redirect_with_params(
                    default_class.id,
                    default_section.id,
                    date_str or today.isoformat(),
                    search_q,
                    None,
                    request.GET.get("page"),
                    _parse_roll_per_page(request.GET.get("per_page")),
                )

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

    from apps.school_data.calendar_policy import academic_year_for_date, resolve_day

    ay_for_att_day = academic_year_for_date(att_date)
    day_calendar = resolve_day(att_date, "student", ay=ay_for_att_day)
    attendance_day_blocked = not day_calendar.is_working_day

    students_with_status = []
    attendance_summary = {"marked": 0, "pending": 0}
    attendance_state = None  # "marked" | "pending" | None
    section_valid_for_class = True
    per_page_param = _parse_roll_per_page(request.GET.get("per_page"))
    roll_call_total = 0
    roll_call_page_obj = None
    roll_call_paginator = None
    roll_call_items = []
    roll_call_qs_base = ""
    if classroom_id and section_id:
        classroom = ClassRoom.objects.filter(pk=classroom_id).first()
        if not classroom:
            section_valid_for_class = False
        elif not classroom.sections.filter(pk=section_id).exists():
            section_valid_for_class = False
        else:
            selected_class_name = getattr(classroom, "name", "") or ""
            selected_section_name = (
                classroom.sections.filter(pk=section_id).values_list("name", flat=True).first() or ""
            )
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
                # Cumulative attendance % (single aggregation; avoids N+1)
                student_ids = [s.id for s in students]
                active_ay_for_pct = get_active_academic_year_obj()
                pct_map = {}
                if student_ids:
                    try:
                        agg_qs = Attendance.objects.filter(student_id__in=student_ids)
                        if active_ay_for_pct is not None:
                            agg_qs = agg_qs.filter(academic_year_id=active_ay_for_pct.id)
                        pct_map = {
                            r["student_id"]: r
                            for r in agg_qs.values("student_id").annotate(
                                total_marked=Count("id"),
                                present_days=Count("id", filter=Q(status=Attendance.Status.PRESENT)),
                                absent_days=Count("id", filter=Q(status=Attendance.Status.ABSENT)),
                                leave_days=Count("id", filter=Q(status=Attendance.Status.LEAVE)),
                            )
                        }
                    except (ProgrammingError, InternalError, DatabaseError):
                        try:
                            connection.rollback()
                        except Exception:
                            pass
                        pct_map = {}
                students_with_status = []
                marked = 0
                for s in students:
                    is_marked = s.id in att_map
                    if is_marked:
                        marked += 1
                    att_pct = None
                    att_pct_band = None
                    att_tooltip = ""
                    agg = pct_map.get(s.id)
                    if agg and agg.get("total_marked"):
                        present = int(agg["present_days"])
                        total_m = int(agg["total_marked"])
                        absent = int(agg["absent_days"])
                        leave = int(agg["leave_days"])
                        att_pct = round((present / total_m) * 100) if total_m else None
                        if att_pct is not None:
                            if att_pct >= 90:
                                att_pct_band = "high"
                            elif att_pct >= 75:
                                att_pct_band = "mid"
                            else:
                                att_pct_band = "low"
                        att_tooltip = (
                            f"Present: {present} · Absent: {absent} · Leave: {leave} · Total marked: {total_m}"
                        )
                    students_with_status.append(
                        {
                            "student": s,
                            "status": att_map.get(s.id, Attendance.Status.PRESENT),
                            "is_marked": is_marked,
                            "att_pct": att_pct,
                            "att_pct_band": att_pct_band,
                            "att_tooltip": att_tooltip,
                        }
                    )
                attendance_summary = {"marked": marked, "pending": max(0, len(students) - marked)}
                attendance_state = "marked" if marked > 0 else "pending"

    roll_call_total = len(students_with_status)
    if roll_call_total:
        pp_int = _roll_per_page_int(per_page_param)
        if pp_int is None:
            paginator = Paginator(students_with_status, max(roll_call_total, 1))
        else:
            paginator = Paginator(students_with_status, pp_int)
        roll_call_page_obj = paginator.get_page(request.GET.get("page"))
        roll_call_paginator = paginator
        _si = roll_call_page_obj.start_index
        row_base = _si() if callable(_si) else _si
        for idx, item in enumerate(roll_call_page_obj.object_list):
            item["row_number"] = row_base + idx
        roll_call_items = roll_call_page_obj.object_list

    params_base = {"attendance_date": date_str, "per_page": per_page_param}
    if classroom_id:
        params_base["classroom_id"] = classroom_id
    if section_id:
        params_base["section_id"] = section_id
    if search_q:
        params_base["q"] = search_q
    if focus_student_id:
        params_base["student_id"] = focus_student_id
    roll_call_qs_base = urlencode(params_base)

    active_ay_ctx = get_active_academic_year_obj()
    attendance_pct_scope_label = (
        f"Active academic year ({active_ay_ctx.name})" if active_ay_ctx else "All recorded sessions"
    )

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
            "roll_call_items": roll_call_items,
            "roll_call_total": roll_call_total,
            "roll_call_page_obj": roll_call_page_obj,
            "roll_call_paginator": roll_call_paginator,
            "per_page_param": per_page_param,
            "roll_call_qs_base": roll_call_qs_base,
            "attendance_summary": attendance_summary,
            "attendance_state": attendance_state,
            "future_date": future_date,
            "section_valid_for_class": section_valid_for_class,
            "status_choices": Attendance.Status.choices,
            "focus_student": focus_student,
            "attendance_pct_scope_label": attendance_pct_scope_label,
            "day_calendar": day_calendar,
            "attendance_day_blocked": attendance_day_blocked,
            "selected_class_name": locals().get("selected_class_name", ""),
            "selected_section_name": locals().get("selected_section_name", ""),
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
            {
                "fee_rows": [],
                "summary": {"gross": 0, "concession": 0, "paid": 0, "balance": 0},
                "filters": {"status": "", "fee_type": ""},
                "fee_type_choices": [],
            },
        )

    fee_qs = (
        Fee.objects.filter(student=student)
        .select_related("fee_structure", "fee_structure__fee_type")
        .prefetch_related("payments")
        .order_by("-due_date")
    )

    # Optional filters
    status_filter = (request.GET.get("status") or "").strip().upper()
    fee_type_filter = (request.GET.get("fee_type") or "").strip()

    rows = []
    total_gross = Decimal("0")
    total_concession = Decimal("0")
    total_paid = Decimal("0")
    total_balance = Decimal("0")
    today = date.today()

    # Build fee type choices from all fees (before filters)
    fee_type_choices = []
    try:
        fee_type_choices = sorted(
            {((f.fee_structure.fee_type.name if f.fee_structure_id and f.fee_structure.fee_type_id else "") or "").strip() for f in fee_qs}
            - {""}
        )
    except Exception:
        fee_type_choices = []

    for fee in fee_qs:
        fee_type_name = (
            (fee.fee_structure.fee_type.name if fee.fee_structure_id and fee.fee_structure.fee_type_id else "")
            or ""
        ).strip()
        if status_filter in ("PENDING", "PARTIAL", "PAID") and fee.status != status_filter:
            continue
        if fee_type_filter and fee_type_name != fee_type_filter:
            continue

        paid_amount = sum(p.amount for p in fee.payments.all())
        gross_amount = fee.amount or Decimal("0")
        concession_amount = fee.total_concession_amount
        net_amount = fee.effective_due_amount
        balance_amount = max(net_amount - paid_amount, Decimal("0"))

        total_gross += gross_amount
        total_concession += concession_amount
        total_paid += paid_amount
        total_balance += balance_amount
        rows.append(
            {
                "fee": fee,
                "fee_type": fee_type_name or (getattr(fee.fee_structure, "line_name", "") or "").strip() or "Fee",
                "gross_amount": gross_amount,
                "concession_amount": concession_amount,
                "net_amount": net_amount,
                "paid_amount": paid_amount,
                "balance_amount": balance_amount,
                "is_unpaid": fee.status in ("PENDING", "PARTIAL"),
                "is_overdue": fee.due_date < today and fee.status in ("PENDING", "PARTIAL"),
            }
        )

    summary = {
        "gross": total_gross,
        "concession": total_concession,
        "paid": total_paid,
        "balance": total_balance,
    }
    return render(
        request,
        "core/student/fees.html",
        {
            "fee_rows": rows,
            "summary": summary,
            "filters": {"status": status_filter, "fee_type": fee_type_filter},
            "fee_type_choices": fee_type_choices,
        },
    )


@login_required
def homework_list(request):
    if not has_feature_access(getattr(request.user, "school", None), "homework", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")

    ensure_homework_enterprise_columns_if_missing(connection)
    ensure_homework_audit_columns_if_missing(connection)

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
        student_hw_statuses = [Homework.Status.PUBLISHED, Homework.Status.CLOSED]
        hw_class_section = []
        if student.section:
            hw_class_section = list(
                Homework.objects.filter(
                    classes=student.classroom,
                    sections=student.section,
                    status__in=student_hw_statuses,
                )
                .defer("attachment")
                .prefetch_related("classes", "sections", "assigned_by")
                .select_related("academic_year")
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
            status__in=student_hw_statuses,
        ).defer("attachment").select_related("subject", "teacher", "teacher__user", "academic_year")
        hw_ids_legacy = set(hw_legacy.values_list("id", flat=True))
        hw_new = [h for h in hw_class_section if h.id not in hw_ids_legacy]
        assignments_raw = list(hw_legacy) + hw_new
        assignments_raw.sort(key=lambda h: (h.due_date, -h.id))

        subject_qs = Subject.objects.filter(id__in=mapped_subject_ids).order_by("display_order", "name")
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
        attempt_count_map = {
            hid: c
            for hid, c in HomeworkSubmissionAttempt.objects.filter(
                student=student,
                homework_id__in=[h.id for h in assignments_raw],
            )
            .values_list("homework_id")
            .annotate(c=Count("id"))
        }

        today = date.today()
        rows = []
        completed = pending = 0
        for hw in assignments_raw:
            sub = submission_map.get(hw.id)
            status = sub.status if sub else HomeworkSubmission.Status.PENDING
            is_missing = (status != HomeworkSubmission.Status.COMPLETED) and (hw.due_date < today)
            is_late = False
            if status == HomeworkSubmission.Status.COMPLETED and sub and sub.submitted_at:
                try:
                    is_late = sub.submitted_at.date() > hw.due_date
                except Exception:
                    is_late = False

            if status == HomeworkSubmission.Status.COMPLETED:
                if is_late:
                    badge = {"text": "Late", "cls": "bg-warning text-dark", "icon": "bi bi-exclamation-triangle"}
                else:
                    badge = {"text": "Submitted", "cls": "bg-success", "icon": "bi bi-check2-circle"}
            else:
                if is_missing:
                    badge = {"text": "Missing", "cls": "bg-danger", "icon": "bi bi-x-circle"}
                else:
                    badge = {"text": "Pending", "cls": "bg-secondary", "icon": "bi bi-clock"}

            if status == HomeworkSubmission.Status.COMPLETED:
                completed += 1
            else:
                pending += 1
            rows.append(
                {
                    "homework": hw,
                    "status": status,
                    "submission": sub,
                    "badge": badge,
                    "is_late": is_late,
                    "is_missing": is_missing,
                    "attempt_count": int(attempt_count_map.get(hw.id) or 0),
                    "is_overdue": hw.is_past_submission_deadline()
                    and status != HomeworkSubmission.Status.COMPLETED,
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
        subject_filter = request.GET.get("subject", "").strip()
        due_status = (request.GET.get("due_status") or "").strip().lower()  # today|overdue|upcoming
        if classroom_filter.isdigit():
            qs = qs.filter(classes__id=int(classroom_filter))
        if subject_filter.isdigit():
            qs = qs.filter(subject_id=int(subject_filter))
        today = timezone.localdate()
        if due_status == "today":
            qs = qs.filter(due_date=today)
        elif due_status == "overdue":
            qs = qs.filter(due_date__lt=today)
        elif due_status == "upcoming":
            qs = qs.filter(due_date__gt=today)
        # qs is already pk-deduped in _homework_queryset_for_teacher; avoid .distinct() here
        # (same PostgreSQL + order_by pitfall as the admin homework list).
        homework_list = qs
        class_ids = set()
        if teacher:
            class_ids.update(
                ClassSectionSubjectTeacher.objects.filter(teacher=teacher).values_list(
                    "class_obj_id", flat=True
                ).distinct()
            )
            class_ids.update(teacher.classrooms.values_list("id", flat=True))
        classrooms = list(ClassRoom.objects.filter(id__in=class_ids).order_by(*ORDER_GRADE_NAME)) if class_ids else []
        subject_ids = list(homework_list.values_list("subject_id", flat=True).distinct())
        subjects = list(Subject.objects.filter(id__in=[sid for sid in subject_ids if sid]).order_by("display_order", "name")) if subject_ids else []
        filtered_count = homework_list.count()
        return render(
            request,
            "core/teacher/homework_list.html",
            {
                "homework_list": homework_list,
                "classrooms": classrooms,
                "subjects": subjects,
                "filters": {"classroom": classroom_filter, "subject": subject_filter, "due_status": due_status},
                "today": today,
                "filtered_count": filtered_count,
            },
        )

    return render(request, "core/placeholders/coming_soon.html", {"title": "Homework"})


@student_required
@feature_required("homework")
@require_POST
def student_homework_submit(request, homework_id):
    """Mark assignment as submitted for current student (no upload required)."""
    student = getattr(request.user, "student_profile", None)
    if not student or not student.classroom:
        messages.error(request, "Student profile is not configured.")
        return redirect("core:homework_list")

    ensure_homework_enterprise_columns_if_missing(connection)

    homework = get_object_or_404(
        Homework.objects.defer("attachment")
        .prefetch_related("classes", "sections")
        .select_related("subject"),
        id=homework_id,
    )
    if not homework.is_visible_to_students():
        raise PermissionDenied
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

    if not homework.allows_new_submission():
        messages.error(request, "Submissions are closed for this assignment.")
        return redirect("core:homework_list")
    if homework.is_past_submission_deadline():
        messages.error(request, "The deadline for this assignment has passed.")
        return redirect("core:homework_list")

    upload = request.FILES.get("submission_file")

    submission, _ = HomeworkSubmission.objects.get_or_create(
        homework=homework,
        student=student,
        defaults={"status": HomeworkSubmission.Status.PENDING},
    )

    # Always keep a history row for audit + "submission history" UI.
    # (Do not rely only on HomeworkSubmission which is unique per student+homework and can be overwritten.)
    HomeworkSubmissionAttempt.objects.create(
        homework=homework,
        student=student,
        submission_file=upload if upload else None,
        remarks=(request.POST.get("remarks") or "").strip(),
    )

    update_fields = ["status", "submitted_at"]
    if upload:
        submission.submission_file = upload
        update_fields.insert(0, "submission_file")
    # Keep remarks on the current submission too (optional, shown to student)
    if "remarks" in request.POST:
        submission.remarks = (request.POST.get("remarks") or "").strip()
        if "remarks" not in update_fields:
            update_fields.append("remarks")
    submission.status = HomeworkSubmission.Status.COMPLETED
    submission.submitted_at = timezone.now()
    submission.save(update_fields=update_fields)

    messages.success(request, f"Assignment submitted for '{homework.title}'.")
    return redirect("core:student_homework_detail", homework_id=homework.id)


@student_required
@feature_required("homework")
@require_GET
def student_homework_detail(request, homework_id: int):
    student = getattr(request.user, "student_profile", None)
    if not student or not student.classroom:
        raise PermissionDenied
    ensure_homework_enterprise_columns_if_missing(connection)
    ensure_homework_audit_columns_if_missing(connection)
    hw = get_object_or_404(
        Homework.objects.defer("attachment")
        .prefetch_related("classes", "sections")
        .select_related("subject", "assigned_by", "academic_year"),
        id=homework_id,
    )
    if not hw.is_visible_to_students():
        raise PermissionDenied
    # Access check: new model (classes+sections) or legacy (subject)
    if hw.classes.exists() or hw.sections.exists():
        if (
            not student.section_id
            or not hw.classes.filter(id=student.classroom_id).exists()
            or not hw.sections.filter(id=student.section_id).exists()
        ):
            raise PermissionDenied
    elif hw.subject_id:
        if not student.section_id:
            raise PermissionDenied
        allowed = ClassSectionSubjectTeacher.objects.filter(
            class_obj_id=student.classroom_id,
            section_id=student.section_id,
            subject_id=hw.subject_id,
        ).exists()
        if not allowed:
            raise PermissionDenied
    else:
        raise PermissionDenied
    submission = HomeworkSubmission.objects.filter(homework_id=hw.id, student=student).first()
    status = submission.status if submission else HomeworkSubmission.Status.PENDING
    attempts_qs = HomeworkSubmissionAttempt.objects.filter(homework_id=hw.id, student=student).order_by("-submitted_at")[:10]
    attempts = []
    for a in attempts_qs:
        is_late = False
        if a.submitted_at and hw.due_date:
            try:
                is_late = a.submitted_at.date() > hw.due_date
            except Exception:
                is_late = False
        attempts.append(
            {
                "submitted_at": a.submitted_at,
                "submission_file": a.submission_file,
                "remarks": a.remarks,
                "is_late": is_late,
            }
        )
    return render(
        request,
        "core/student/homework_detail.html",
        {
            "homework": hw,
            "student": student,
            "submission": submission,
            "submission_attempts": attempts,
            "status": status,
            "today": timezone.localdate(),
            "is_overdue": hw.is_past_submission_deadline() and status != HomeworkSubmission.Status.COMPLETED,
        },
    )


def _eligible_students_for_homework(hw: Homework):
    """
    Students eligible for a homework (for submission stats).
    Supports:
    - New homework scope: hw.classes + hw.sections
    - Legacy scope: hw.subject via ClassSectionSubjectTeacher class+section pairs
    """
    if hw.classes.exists() or hw.sections.exists():
        class_ids = list(hw.classes.values_list("id", flat=True))
        section_ids = list(hw.sections.values_list("id", flat=True))
        qs = Student.objects.all()
        if class_ids:
            qs = qs.filter(classroom_id__in=class_ids)
        if section_ids:
            qs = qs.filter(section_id__in=section_ids)
        return qs
    if hw.subject_id:
        pairs = list(
            ClassSectionSubjectTeacher.objects.filter(subject_id=hw.subject_id)
            .values_list("class_obj_id", "section_id")
            .distinct()
        )
        if not pairs:
            return Student.objects.none()
        conds = [Q(classroom_id=c, section_id=s) for c, s in pairs if c and s]
        if not conds:
            return Student.objects.none()
        return Student.objects.filter(reduce(_or_, conds))
    return Student.objects.none()


@teacher_or_admin_required
@feature_required("homework")
@require_GET
def homework_submission_stats(request, pk: int):
    """
    Teacher/Admin: total/submitted/not-submitted counts and lists for a homework.
    """
    ensure_homework_enterprise_columns_if_missing(connection)
    ensure_homework_audit_columns_if_missing(connection)

    if getattr(request.user, "role", None) == "TEACHER":
        teacher = getattr(request.user, "teacher_profile", None)
        hw = get_object_or_404(_homework_queryset_for_teacher(teacher, request.user), pk=pk)
    else:
        hw = get_object_or_404(
            Homework.objects.defer("attachment")
            .prefetch_related("classes", "sections")
            .select_related("subject", "assigned_by", "teacher", "teacher__user", "academic_year", "modified_by"),
            pk=pk,
        )

    eligible_students = list(
        _eligible_students_for_homework(hw)
        .select_related("user", "classroom", "section")
        .order_by("admission_number", "id")
    )
    total_students = len(eligible_students)

    submitted_ids = set(
        HomeworkSubmission.objects.filter(
            homework_id=hw.id,
            status=HomeworkSubmission.Status.COMPLETED,
        ).values_list("student_id", flat=True)
    )
    submitted_students = [s for s in eligible_students if s.id in submitted_ids]
    not_submitted_students = [s for s in eligible_students if s.id not in submitted_ids]

    return render(
        request,
        "core/homework/submission_stats.html",
        {
            "hw": hw,
            "total_students": total_students,
            "submitted_count": len(submitted_students),
            "not_submitted_count": len(not_submitted_students),
            "submitted_students": submitted_students,
            "not_submitted_students": not_submitted_students,
            "is_teacher": getattr(request.user, "role", None) == "TEACHER",
        },
    )


def _school_homework_list_queryset(request):
    # Omit attachment in SELECT when tenant DB predates migration 0037/0040 (column may be missing).
    # Dedupe by primary key via a subquery instead of .distinct() + .order_by() on the main row,
    # which can return no rows on PostgreSQL for some join shapes.
    ensure_homework_enterprise_columns_if_missing(connection)
    ensure_homework_audit_columns_if_missing(connection)
    base = Homework.objects.defer("attachment")
    classroom_filter = request.GET.get("classroom", "").strip()
    if classroom_filter.isdigit():
        base = base.filter(classes__id=int(classroom_filter))
    pk_subq = base.values("pk").distinct()
    qs = (
        Homework.objects.filter(pk__in=pk_subq)
        .defer("attachment")
        .prefetch_related("classes", "sections")
        .select_related("assigned_by", "subject", "teacher", "teacher__user", "academic_year", "modified_by")
        .order_by("-due_date", "-created_at")
    )
    return qs, classroom_filter


def _homework_academic_year_display(hw):
    if not hw.academic_year_id:
        return "—"
    try:
        return hw.academic_year.name
    except Exception:
        return "—"


def _homework_row_payload(hw, today):
    teacher_disp = ""
    try:
        if hw.teacher_id and getattr(hw.teacher, "user", None):
            u = hw.teacher.user
            teacher_disp = u.get_full_name() or u.username or ""
    except Exception:
        teacher_disp = ""
    assigned = ""
    try:
        if hw.assigned_by_id:
            assigned = hw.assigned_by.get_full_name() or hw.assigned_by.username or ""
    except Exception:
        assigned = ""
    created_by_line = assigned or teacher_disp or "—"
    class_ids = [c.id for c in hw.classes.all()]
    section_ids = [s.id for s in hw.sections.all()]
    classes_names = ", ".join(c.name for c in hw.classes.all())
    sections_names = ", ".join(s.name for s in hw.sections.all())
    # Do not touch hw.attachment here: list queryset uses defer("attachment") and the column
    # may be missing on some tenants — any SELECT for attachment aborts Postgres transactions.
    att_url = ""
    att_name = ""
    overdue = hw.is_past_submission_deadline()
    assigned_date_iso = hw.assigned_date.isoformat() if hw.assigned_date else ""
    assigned_date_display = hw.assigned_date.strftime("%d %b %Y") if hw.assigned_date else "—"
    late_until_iso = ""
    if hw.late_submission_until:
        late_until_iso = timezone.localtime(hw.late_submission_until).strftime("%Y-%m-%dT%H:%M")
    modified_by = ""
    try:
        if getattr(hw, "modified_by_id", None):
            modified_by = hw.modified_by.get_full_name() or hw.modified_by.username or ""
    except Exception:
        modified_by = ""
    return {
        "id": hw.id,
        "subject": hw.subject.name if hw.subject_id else "",
        "subject_id": hw.subject_id,
        "title": hw.title,
        "description": hw.description,
        "instructions": hw.instructions or "",
        "class_ids": class_ids,
        "section_ids": section_ids,
        "classes_display": classes_names or "—",
        "sections_display": sections_names or "—",
        "created_display": hw.created_at.strftime("%d %b %Y, %H:%M") if hw.created_at else "—",
        "modified_display": timezone.localtime(hw.modified_on).strftime("%d %b %Y, %H:%M") if getattr(hw, "modified_on", None) else "—",
        "modified_by": modified_by or "—",
        "assigned_date_iso": assigned_date_iso,
        "assigned_date_display": assigned_date_display,
        "due_date_iso": hw.due_date.isoformat(),
        "due_date_display": hw.due_date.strftime("%d %b %Y"),
        "homework_type": hw.homework_type,
        "homework_type_display": hw.get_homework_type_display(),
        "submission_type": hw.submission_type,
        "submission_type_display": hw.get_submission_type_display(),
        "max_marks": hw.max_marks,
        "estimated_duration_minutes": hw.estimated_duration_minutes,
        "priority": hw.priority,
        "priority_display": hw.get_priority_display(),
        "workflow_status": hw.status,
        "workflow_status_display": hw.get_status_display(),
        "allow_late_submission": hw.allow_late_submission,
        "late_submission_until_iso": late_until_iso,
        "submission_required": hw.submission_required,
        "academic_year_id": hw.academic_year_id,
        "academic_year_display": _homework_academic_year_display(hw),
        "assigned_by_id": hw.assigned_by_id,
        "attachment_name": att_name,
        "attachment_url": att_url,
        "created_by": created_by_line,
        "status": "Overdue" if overdue else "Active",
    }


def _school_homework_list_context(request, homework_list, homework_edit_form=None, homework_edit_id=None):
    from .forms import HomeworkCreateForm

    today = date.today()
    payload = {str(hw.id): _homework_row_payload(hw, today) for hw in homework_list}
    edit_form = homework_edit_form if homework_edit_form is not None else HomeworkCreateForm(user=request.user)
    classroom_filter = (request.GET.get("classroom") or "").strip()
    try:
        with transaction.atomic():
            classrooms = list(
                ClassRoom.objects.select_related("academic_year").order_by(*ORDER_AY_START_GRADE_NAME)
            )
    except (ProgrammingError, InternalError, DatabaseError, OperationalError):
        classrooms = []
    return {
        "homework_list": homework_list,
        "classrooms": classrooms,
        "filters": {"classroom": classroom_filter},
        "today": today,
        "homework_edit_form": edit_form,
        "homework_edit_id": homework_edit_id,
        "homework_payload": payload,
    }


@admin_required
@feature_required("homework")
def school_homework_list(request):
    """Admin: view all homework."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    try:
        if getattr(connection, "needs_rollback", False):
            connection.rollback()
    except Exception:
        pass
    qs, _ = _school_homework_list_queryset(request)
    # Nested atomic = savepoint: if list(qs) fails (e.g. missing columns on a tenant),
    # PostgreSQL aborts only the savepoint so the request transaction stays usable for
    # template queries (HomeworkCreateForm widgets) under ATOMIC_REQUESTS.
    try:
        with transaction.atomic():
            homework_list = list(qs)
    except (ProgrammingError, InternalError, DatabaseError, OperationalError):
        homework_list = []
    ctx = _school_homework_list_context(request, homework_list)
    return render(request, "core/school/homework_list.html", ctx)


@admin_required
@feature_required("homework")
def school_homework_update(request, pk):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import HomeworkCreateForm

    ensure_homework_enterprise_columns_if_missing(connection)
    ensure_homework_audit_columns_if_missing(connection)
    hw = get_object_or_404(Homework.objects.defer("attachment"), pk=pk)
    if request.method != "POST":
        return redirect("core:school_homework_list")
    form = HomeworkCreateForm(request.POST, request.FILES, instance=hw, user=request.user)
    if form.is_valid():
        obj = form.save(commit=False)
        if not obj.assigned_by_id:
            obj.assigned_by = request.user
        obj.save_with_audit(request.user)
        form.save_m2m()
        messages.success(request, "Homework updated successfully.")
        return redirect("core:school_homework_list")
    qs, _ = _school_homework_list_queryset(request)
    try:
        with transaction.atomic():
            homework_list = list(qs)
    except (ProgrammingError, InternalError, DatabaseError, OperationalError):
        homework_list = []
    ctx = _school_homework_list_context(
        request, homework_list, homework_edit_form=form, homework_edit_id=hw.id
    )
    return render(request, "core/school/homework_list.html", ctx)


@admin_required
@feature_required("homework")
def school_homework_delete(request, pk):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    if request.method != "POST":
        return redirect("core:school_homework_list")
    ensure_homework_enterprise_columns_if_missing(connection)
    ensure_homework_audit_columns_if_missing(connection)
    hw = get_object_or_404(Homework.objects.defer("attachment"), pk=pk)
    title = hw.title
    hw.delete()
    messages.success(request, f'Homework "{title}" was deleted.')
    return redirect("core:school_homework_list")


@admin_required
@feature_required("homework")
def school_homework_create(request):
    """Admin: create homework with class+section assignment."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import HomeworkCreateForm

    ensure_homework_enterprise_columns_if_missing(connection)
    ensure_homework_audit_columns_if_missing(connection)
    if request.method == "POST":
        form = HomeworkCreateForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            hw = form.save(commit=False)
            if not hw.assigned_by_id:
                hw.assigned_by = request.user
            hw.save_with_audit(request.user)
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
                qs.order_by(
                    "classroom__grade_order",
                    "classroom__name",
                    "section__name",
                    "roll_number",
                    "user__first_name",
                )
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

    ensure_homework_enterprise_columns_if_missing(connection)
    ensure_homework_audit_columns_if_missing(connection)
    if request.method == "POST":
        form = HomeworkCreateForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            hw = form.save(commit=False)
            if not hw.assigned_by_id:
                hw.assigned_by = request.user
            hw.teacher = teacher
            hw.save_with_audit(request.user)
            form.save_m2m()
            messages.success(request, "Homework created successfully.")
            return redirect("core:homework_list")
    else:
        form = HomeworkCreateForm(user=request.user)

    return render(request, "core/teacher/homework_form.html", {"form": form, "title": "Create Homework"})


@teacher_or_admin_required
@feature_required("homework")
def teacher_homework_view(request, pk: int):
    teacher = getattr(request.user, "teacher_profile", None)
    if getattr(request.user, "role", None) == User.Roles.TEACHER and not teacher:
        raise PermissionDenied
    ensure_homework_enterprise_columns_if_missing(connection)
    ensure_homework_audit_columns_if_missing(connection)
    hw = get_object_or_404(
        Homework.objects.defer("attachment")
        .select_related("subject", "assigned_by", "teacher", "teacher__user", "academic_year", "modified_by")
        .prefetch_related("classes", "sections"),
        pk=pk,
    )
    # Teacher can only view if explicitly assigned/created for them (or visible via their scope).
    if getattr(request.user, "role", None) == User.Roles.TEACHER:
        allowed = _homework_queryset_for_teacher(teacher, request.user).filter(pk=hw.pk).exists()
        if not allowed:
            logger.warning(
                "teacher_homework_view denied user_id=%s teacher_id=%s hw_id=%s hw_teacher_id=%s hw_assigned_by_id=%s",
                getattr(request.user, "id", None),
                getattr(teacher, "id", None),
                hw.id,
                getattr(hw, "teacher_id", None),
                getattr(hw, "assigned_by_id", None),
            )
            raise PermissionDenied
    return render(request, "core/teacher/homework_view.html", {"hw": hw})


@teacher_or_admin_required
@feature_required("homework")
def teacher_homework_edit(request, pk: int):
    from .forms import HomeworkCreateForm

    teacher = getattr(request.user, "teacher_profile", None)
    if getattr(request.user, "role", None) == User.Roles.TEACHER and not teacher:
        raise PermissionDenied
    ensure_homework_enterprise_columns_if_missing(connection)
    ensure_homework_audit_columns_if_missing(connection)
    hw = get_object_or_404(Homework.objects.defer("attachment"), pk=pk)
    if getattr(request.user, "role", None) == User.Roles.TEACHER:
        allowed = _homework_queryset_for_teacher(teacher, request.user).filter(pk=hw.pk).exists()
        if not allowed:
            logger.warning(
                "teacher_homework_edit denied(scope) user_id=%s teacher_id=%s hw_id=%s hw_teacher_id=%s hw_assigned_by_id=%s",
                getattr(request.user, "id", None),
                getattr(teacher, "id", None),
                hw.id,
                getattr(hw, "teacher_id", None),
                getattr(hw, "assigned_by_id", None),
            )
            raise PermissionDenied
    if request.method == "POST":
        form = HomeworkCreateForm(request.POST, request.FILES, instance=hw, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            # Preserve the assigned teacher when editing from teacher portal (teacher role only).
            if getattr(request.user, "role", None) == User.Roles.TEACHER:
                obj.teacher = teacher
            if not obj.assigned_by_id:
                obj.assigned_by = request.user
            obj.save_with_audit(request.user)
            form.save_m2m()
            messages.success(request, "Homework updated.")
            return redirect("core:homework_list")
    else:
        form = HomeworkCreateForm(instance=hw, user=request.user)
    return render(request, "core/teacher/homework_form.html", {"form": form, "title": "Edit Homework"})


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
                from .utils import teacher_allowed_class_section_pairs_lower

                pair_ok = (
                    student.classroom.name.strip().lower(),
                    student.section.name.strip().lower(),
                ) in teacher_allowed_class_section_pairs_lower(teacher)
                subj_ids = set(teacher.subjects.values_list("id", flat=True))
                if teacher.subject_id:
                    subj_ids.add(teacher.subject_id)
                allowed = pair_ok and subject.id in subj_ids
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
        form.fields["subject"].queryset = Subject.objects.filter(id__in=mapped_subject_ids).order_by("display_order", "name")

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
            .select_related("session", "subject", "teacher__user")
            .defer(
                "session__updated_at",
                "session__display_order",
                "session__modified_by",
                "session__modified_at",
            )
        )
    else:
        qs = Exam.objects.none()

    papers = list(qs.order_by("-session_id", "-date", "subject__name"))
    session_groups = []
    seen_session = set()
    for p in papers:
        if not p.session_id:
            continue
        if p.session_id in seen_session:
            continue
        seen_session.add(p.session_id)
        sess = p.session
        if not sess:
            continue
        sess_papers = [x for x in papers if x.session_id == p.session_id]
        sess_papers.sort(key=lambda x: (x.date or date.min, x.subject_id or 0))
        if sess_papers:
            dts = [x.date for x in sess_papers if x.date]
            session_groups.append({
                "session": sess,
                "papers": sess_papers,
                "date_min": min(dts) if dts else None,
                "date_max": max(dts) if dts else None,
            })

    session_groups.sort(key=lambda g: (g["session"].created_at, g["session"].pk), reverse=True)

    return render(
        request,
        "core/teacher/exams.html",
        {
            "exam_session_groups": session_groups,
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
        _examsession_queryset().select_related("classroom"),
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


def _parse_iso_date_param(val):
    if not val or not str(val).strip():
        return None
    try:
        return date.fromisoformat(str(val).strip())
    except (ValueError, TypeError):
        return None


def _exam_session_card_status(session, today):
    """UI status for a session row (uses annotated paper_count / date_min / date_max)."""
    pc = getattr(session, "paper_count", 0) or 0
    dmin = getattr(session, "date_min", None)
    dmax = getattr(session, "date_max", None) or dmin
    if pc == 0 or dmin is None:
        return "draft", "Draft"
    if dmin > today:
        return "upcoming", "Upcoming"
    if dmax is not None and dmax < today:
        return "completed", "Completed"
    return "ongoing", "Ongoing"


@admin_required
@feature_required("exams")
def school_exams_list(request):
    """
    School Admin: exam sessions (grouped multi-subject papers).
    Default order: latest paper dates first, then by session created time.
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
    ex_session = request.GET.get("ex_session") or ""
    exam_status = (request.GET.get("exam_status") or "").strip().lower()
    date_from_s = request.GET.get("date_from") or ""
    date_to_s = request.GET.get("date_to") or ""

    try:
        if getattr(connection, "needs_rollback", False):
            connection.rollback()
    except Exception:
        pass

    today = timezone.localdate()
    exam_sessions_enabled = True
    exam_sessions = []
    session_picklist = []

    def _session_base_qs():
        # Do not select_related("created_by"): INNER JOIN hides sessions if that user row is missing.
        return _examsession_queryset().select_related("classroom")

    def _attach_session_card_labels(rows):
        for s in rows:
            st_key, st_label = _exam_session_card_status(s, today)
            s.card_status_key = st_key
            s.card_status_label = st_label

    try:
        session_qs = _session_base_qs().annotate(
            paper_count=Count("papers", distinct=True),
            date_min=Min("papers__date"),
            date_max=Max("papers__date"),
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

        if ex_session.isdigit():
            session_qs = session_qs.filter(pk=int(ex_session))

        if exam_status in {"upcoming", "completed", "ongoing", "draft"}:
            if exam_status == "draft":
                session_qs = session_qs.filter(Q(paper_count=0) | Q(date_min__isnull=True))
            elif exam_status == "upcoming":
                session_qs = session_qs.filter(date_min__gt=today)
            elif exam_status == "completed":
                session_qs = session_qs.filter(date_max__lt=today).exclude(date_max__isnull=True)
            elif exam_status == "ongoing":
                session_qs = (
                    session_qs.filter(date_min__lte=today)
                    .filter(Q(date_max__gte=today) | Q(date_max__isnull=True))
                    .exclude(date_min__isnull=True)
                )

        df = _parse_iso_date_param(date_from_s)
        dt = _parse_iso_date_param(date_to_s)
        if df and dt:
            session_qs = session_qs.filter(date_max__gte=df, date_min__lte=dt)
        elif df:
            session_qs = session_qs.filter(date_max__gte=df)
        elif dt:
            session_qs = session_qs.filter(date_min__lte=dt)

        session_qs = session_qs.order_by(
            F("date_max").desc(nulls_last=True),
            F("date_min").desc(nulls_last=True),
            "-created_at",
            "-id",
        )
        exam_sessions = list(session_qs)
        _attach_session_card_labels(exam_sessions)

        session_picklist = list(
            _session_base_qs().order_by(Lower("name"), "id").values("id", "name")[:400]
        )
    except (ProgrammingError, InternalError, DatabaseError, OperationalError):
        try:
            connection.rollback()
        except Exception:
            pass
        # Fallback: list sessions without paper aggregates (e.g. missing session_id on exam).
        try:
            fq = _session_base_qs()
            if class_id:
                try:
                    classroom_obj = ClassRoom.objects.get(id=class_id)
                    fq = fq.filter(class_name=classroom_obj.name)
                except ClassRoom.DoesNotExist:
                    fq = ExamSession.objects.none()
            if section_id:
                try:
                    sec = Section.objects.get(id=section_id)
                    fq = fq.filter(section__iexact=sec.name)
                except Section.DoesNotExist:
                    fq = ExamSession.objects.none()
            if q:
                fq = fq.filter(name__icontains=q)
            if ex_session.isdigit():
                fq = fq.filter(pk=int(ex_session))
            fq = fq.order_by("-created_at", "-id")
            exam_sessions = list(fq)
            for s in exam_sessions:
                s.paper_count = 0
                s.date_min = None
                s.date_max = None
            _attach_session_card_labels(exam_sessions)
            session_picklist = list(
                _session_base_qs().order_by(Lower("name"), "id").values("id", "name")[:400]
            )
            exam_sessions_enabled = True
        except (ProgrammingError, InternalError, DatabaseError, OperationalError):
            exam_sessions_enabled = False
            exam_sessions = []
            session_picklist = []
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
        classrooms = list(ClassRoom.objects.all().order_by(*ORDER_AY_START_GRADE_NAME))
        sections = list(Section.objects.all().order_by("name"))
        teachers = (
            list(
                Teacher.objects.filter(user__school=school)
                .select_related("user")
                .order_by("user__first_name", "user__last_name")
            )
            if school
            else []
        )
        subjects = list(Subject.objects.all().order_by("display_order", "name"))
    except (ProgrammingError, InternalError, DatabaseError, OperationalError):
        try:
            connection.rollback()
        except Exception:
            pass
        classrooms = []
        sections = []
        teachers = []
        subjects = []

    filters_ctx = {
        "classroom": class_id,
        "section": section_id,
        "teacher": teacher_id,
        "subject": subject_id,
        "q": q,
        "ex_session": ex_session,
        "exam_status": exam_status,
        "date_from": date_from_s,
        "date_to": date_to_s,
    }

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
            "session_picklist": session_picklist,
            "filters": filters_ctx,
            "can_manage_exam_session_cards": _can_manage_exam_session_admin_actions(request.user),
        },
    )


@admin_required
@feature_required("exams")
def school_exam_session_edit(request, session_id):
    """School admin: edit exam session and all subject papers (dates, teachers, marks lock, etc.)."""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    if not _can_manage_exam_session_admin_actions(request.user):
        return HttpResponseForbidden("Only school administrators can edit exam sessions.")

    session_obj = get_object_or_404(
        _examsession_queryset().select_related("classroom"),
        pk=session_id,
    )

    paper_qs = (
        Exam.objects.filter(session=session_obj)
        .select_related("subject", "teacher__user")
        .order_by("date", "subject__name")
    )

    if request.method == "POST":
        form = SchoolExamSessionEditForm(request.POST, instance=session_obj)
        formset = ExamSessionPaperFormSet(
            request.POST,
            instance=session_obj,
            queryset=paper_qs,
            school=school,
        )
        form_ok = form.is_valid()
        formset.session_meta = form.cleaned_data if form_ok else {}
        formset_ok = formset.is_valid()
        if form_ok and formset_ok:
            with transaction.atomic():
                obj = form.save(commit=False)
                if obj.classroom_id and not (obj.class_name or "").strip():
                    obj.class_name = obj.classroom.name
                obj.modified_by = request.user
                obj.modified_at = timezone.now()
                try:
                    obj.save()
                except ProgrammingError:
                    try:
                        connection.rollback()
                    except Exception:
                        pass
                    ExamSession.objects.filter(pk=obj.pk).update(
                        name=obj.name,
                        class_name=obj.class_name,
                        section=obj.section,
                        classroom=obj.classroom,
                    )

                saved = formset.save(commit=False)
                for del_obj in formset.deleted_objects:
                    del_obj.delete()
                for paper in saved:
                    paper.session = obj
                    paper.class_name = obj.class_name
                    paper.section = obj.section
                    paper.classroom = obj.classroom
                    if paper.subject_id and not (paper.name or "").strip():
                        paper.name = paper.subject.name[:100]
                    if paper.total_marks is None:
                        paper.total_marks = 100
                    if not paper.created_by_id:
                        paper.created_by = request.user
                    paper.save()
                formset.save_m2m()
                from apps.core.exam_components import sync_exam_mark_components

                for form in formset.forms:
                    if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                        continue
                    if form.cleaned_data.get("DELETE"):
                        continue
                    ex = form.instance
                    if not ex.pk:
                        continue
                    raw_mc = form.cleaned_data.get("mark_components_json")
                    sync_exam_mark_components(ex, raw_mc or "[]", skip_if_blank=False)

            logger.info(
                "exam_session_updated",
                extra={
                    "user_id": request.user.id,
                    "session_id": obj.pk,
                    "session_name": obj.name,
                },
            )
            messages.success(request, "Exam session and subject papers were updated.")
            return redirect("core:school_exam_session_detail", session_id=obj.pk)
    else:
        form = SchoolExamSessionEditForm(instance=session_obj)
        formset = ExamSessionPaperFormSet(
            instance=session_obj,
            queryset=paper_qs,
            school=school,
        )

    return render(
        request,
        "core/school/exam_session_edit.html",
        {
            "session": session_obj,
            "form": form,
            "formset": formset,
        },
    )


@admin_required
@feature_required("exams")
@require_POST
def school_exam_session_delete(request, session_id):
    """School admin: delete an exam session and all papers (cascade)."""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    if not _can_manage_exam_session_admin_actions(request.user):
        return HttpResponseForbidden("Only school administrators can delete exam sessions.")

    row = ExamSession.objects.filter(pk=session_id).values("id", "name").first()
    if not row:
        messages.warning(
            request,
            "That exam session was not found. It may have been removed, or the link is out of date.",
        )
        return redirect("core:school_exams_list")
    sid = row["id"]
    name = row["name"] or ""
    paper_count = Exam.objects.filter(session_id=sid).count()

    with transaction.atomic():
        ExamSession.objects.filter(pk=sid).delete()

    logger.info(
        "exam_session_deleted",
        extra={
            "user_id": request.user.id,
            "session_id": sid,
            "session_name": name,
            "papers_deleted": paper_count,
        },
    )
    messages.success(request, f"Exam session “{name}” and its papers were removed.")
    return redirect("core:school_exams_list")


@admin_required
@feature_required("exams")
def school_exam_session_detail(request, session_id):
    """Admin: papers (subjects + dates) under one exam session."""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")

    try:
        session_obj = (
            _examsession_queryset()
            .select_related("classroom")
            .get(pk=session_id)
        )
    except ExamSession.DoesNotExist:
        messages.warning(
            request,
            "That exam session was not found. It may have been removed, or the link is out of date.",
        )
        return redirect("core:school_exams_list")

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
            "can_manage_exam_session": _can_manage_exam_session_admin_actions(request.user),
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


def _exam_class_section_date_conflict_outside_session(
    class_name, section_name, dt, session_id, exclude_pk=None
):
    """
    Same as _exam_class_section_date_conflict but ignores papers already in this session
    (a session may hold multiple subjects on the same day).
    """
    qs = Exam.objects.filter(class_name=class_name, section__iexact=section_name, date=dt).exclude(
        session_id=session_id
    )
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


def _exam_times_from_post(request, key_suffix):
    """
    Read exam_start_time_{suffix} and exam_end_time_{suffix} from POST.
    Returns (None, None) if both empty; (start, end) time objects if both set.
    Raises ValueError if only one is set, parsing fails, or end <= start.
    """
    from datetime import datetime

    raw_st = (request.POST.get(f"exam_start_time_{key_suffix}") or "").strip()
    raw_et = (request.POST.get(f"exam_end_time_{key_suffix}") or "").strip()
    if not raw_st and not raw_et:
        return None, None
    if not raw_st or not raw_et:
        raise ValueError("set both start and end time, or leave both empty")

    def _parse(s):
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).time()
            except ValueError:
                continue
        raise ValueError("invalid time")

    st = _parse(raw_st)
    et = _parse(raw_et)
    if et <= st:
        raise ValueError("end time must be after start time")
    return st, et


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
    return (
        Exam.objects.select_related("subject", "teacher__user", "created_by", "session")
        .defer(
            "session__updated_at",
            "session__display_order",
            "session__modified_by",
            "session__modified_at",
        )
    )


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
                            use_mc = request.POST.get("scheduler_use_mark_components") == "1"
                            comp_map_parsed = {}
                            comp_err = False
                            if use_mc:
                                try:
                                    raw_map = (request.POST.get("scheduler_components_map_json") or "{}").strip()
                                    comp_map_parsed = json.loads(raw_map) if raw_map else {}
                                    if not isinstance(comp_map_parsed, dict):
                                        raise ValueError("not a dict")
                                except (TypeError, ValueError, json.JSONDecodeError):
                                    comp_err = True
                                    messages.error(
                                        request,
                                        "Mark components map must be valid JSON object keyed by subject id.",
                                    )
                            papers_created = 0
                            sessions_created = 0
                            skipped = []
                            if not comp_err:
                                try:
                                    from django.core.exceptions import ValidationError as DJValidationError

                                    from apps.core.exam_components import sync_exam_mark_components

                                    with transaction.atomic():
                                        for classroom, sn in pair_items:
                                            cn = classroom.name
                                            session = examsession_create_compat(
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
                                                try:
                                                    start_t, end_t = _exam_times_from_post(request, str(subj_id))
                                                except ValueError as exc:
                                                    skipped.append(f"{subj.name}: {exc}")
                                                    continue
                                                paper_name = subj.name[:100]
                                                raw_tid = (request.POST.get(f"exam_teacher_{subj_id}") or "").strip()
                                                chosen_teacher = None
                                                if raw_tid.isdigit():
                                                    chosen_teacher = _exam_teacher_for_school(school, int(raw_tid))
                                                paper_teacher = chosen_teacher or _default_teacher_for_class_section_subject(
                                                    school, classroom, cn, sn, subj
                                                )
                                                paper = Exam.objects.create(
                                                    session=session,
                                                    name=paper_name,
                                                    classroom=classroom,
                                                    class_name=cn,
                                                    section=sn,
                                                    date=dt,
                                                    start_time=start_t,
                                                    end_time=end_t,
                                                    subject=subj,
                                                    total_marks=tm,
                                                    teacher=paper_teacher,
                                                    created_by=request.user,
                                                )
                                                if use_mc:
                                                    arr = comp_map_parsed.get(str(subj_id))
                                                    if arr is None:
                                                        arr = comp_map_parsed.get(subj_id)
                                                    raw_c = json.dumps(arr) if isinstance(arr, list) else "[]"
                                                    sync_exam_mark_components(paper, raw_c, skip_if_blank=False)
                                                papers_created += 1
                                except DJValidationError as exc:
                                    msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                                    messages.error(request, msg)
                                    sessions_created = 0
                                    papers_created = 0
                                    skipped = []
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
                    try:
                        from django.core.exceptions import ValidationError as DJValidationError

                        from apps.core.exam_components import sync_exam_mark_components

                        with transaction.atomic():
                            session = examsession_create_compat(
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
                            paper = Exam.objects.create(
                                session=session,
                                name=(subj.name[:100] if subj else session_name),
                                classroom=classroom_obj,
                                class_name=cn,
                                section=sn,
                                date=dt,
                                start_time=single_form.cleaned_data.get("start_time"),
                                end_time=single_form.cleaned_data.get("end_time"),
                                room=(single_form.cleaned_data.get("room") or "").strip()[:120],
                                details=(single_form.cleaned_data.get("details") or "").strip(),
                                topics=(single_form.cleaned_data.get("topics") or "").strip(),
                                subject=subj,
                                total_marks=tm,
                                teacher=paper_teacher,
                                created_by=request.user,
                            )
                            if request.POST.get("single_use_mark_components") == "1":
                                sync_exam_mark_components(
                                    paper,
                                    request.POST.get("single_mark_components_json") or "[]",
                                    skip_if_blank=False,
                                )
                    except DJValidationError as exc:
                        msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                        messages.error(request, msg)
                    else:
                        messages.success(
                            request,
                            "Exam session created with one subject paper. Add more papers from Create exam (scheduler) or edit workflow.",
                        )
                        return redirect("core:school_exam_session_detail", session_id=session.pk)

    class_sections = {}
    for c in ClassRoom.objects.prefetch_related("sections").order_by(*ORDER_AY_START_GRADE_NAME):
        class_sections[c.name] = [s.name for s in c.sections.order_by("name")]

    scheduler_class_sections = []
    for c in ClassRoom.objects.prefetch_related("sections").order_by(*ORDER_AY_START_GRADE_NAME):
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
            "all_subjects": Subject.objects.order_by("display_order", "name"),
            "all_classrooms": ClassRoom.objects.select_related("academic_year").order_by(*ORDER_AY_START_GRADE_NAME),
            "class_sections_json": json.dumps(class_sections),
            "scheduler_class_sections_json": json.dumps(scheduler_class_sections, default=str),
            "existing_exams_json": json.dumps(
                [{"d": str(x["date"]), "c": x["class_name"], "s": x["section"], "sub": x["subject_id"]} for x in existing],
                default=str,
            ),
        },
    )


@admin_required
@feature_required("exams")
@require_POST
def api_exam_create(request):
    """
    JSON API: create one exam session and one paper per subject entry.
    Expects Content-Type application/json and CSRF token (cookie + header for fetch).

    Body keys:
      exam (or session_name), class_name, section,
      subjects: [{ subject_id, date (ISO), components?: [{name, marks}], total_marks?: int }]
    """
    school = request.user.school
    if not school:
        return JsonResponse({"ok": False, "error": "Invalid school context"}, status=403)
    try:
        payload = json.loads(request.body.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    exam_name = (payload.get("exam") or payload.get("session_name") or "").strip()
    cn = (payload.get("class_name") or "").strip()
    sn = (payload.get("section") or "").strip()
    subjects_payload = payload.get("subjects") or []
    if not exam_name:
        return JsonResponse({"ok": False, "error": "exam / session_name is required"}, status=400)
    if not cn or not sn:
        return JsonResponse({"ok": False, "error": "class_name and section are required"}, status=400)
    if not isinstance(subjects_payload, list) or not subjects_payload:
        return JsonResponse({"ok": False, "error": "subjects must be a non-empty array"}, status=400)

    classroom_obj = (
        ClassRoom.objects.filter(name__iexact=cn)
        .select_related("academic_year")
        .order_by("-academic_year__start_date", "id")
        .first()
    )

    from django.core.exceptions import ValidationError as DJValidationError

    from apps.core.exam_components import sync_exam_mark_components

    try:
        with transaction.atomic():
            session = examsession_create_compat(
                name=exam_name[:100],
                class_name=cn,
                section=sn,
                classroom=classroom_obj,
                created_by=request.user,
            )
            created_ids = []
            for item in subjects_payload:
                if not isinstance(item, dict):
                    raise DJValidationError("Each subject entry must be an object.")
                sid = item.get("subject_id")
                try:
                    sid = int(sid)
                except (TypeError, ValueError):
                    raise DJValidationError("subject_id must be an integer.")
                subj = Subject.objects.filter(pk=sid).first()
                if not subj:
                    raise DJValidationError(f"Unknown subject_id {sid}.")
                raw_d = item.get("date")
                try:
                    dt = date.fromisoformat(str(raw_d))
                except (ValueError, TypeError):
                    raise DJValidationError(f"Invalid date for subject {sid}.")
                if _exam_duplicate(cn, sn, dt, subj):
                    raise DJValidationError(
                        f"An exam already exists for {subj.name} on {dt.isoformat()} for this class and section."
                    )
                if _exam_class_section_date_conflict(cn, sn, dt):
                    raise DJValidationError(
                        f"Class {cn} section {sn} already has another exam on {dt.isoformat()}."
                    )
                components = item.get("components")
                if components is None:
                    components = []
                if not isinstance(components, list):
                    raise DJValidationError("components must be an array when provided.")
                tm_raw = item.get("total_marks")
                if components:
                    tm = 100
                else:
                    try:
                        tm = int(tm_raw) if tm_raw is not None else 100
                    except (TypeError, ValueError):
                        raise DJValidationError("total_marks must be an integer when components are empty.")
                    if tm < 1:
                        raise DJValidationError("total_marks must be at least 1.")

                paper_teacher = _default_teacher_for_class_section_subject(
                    school, classroom_obj, cn, sn, subj
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
                    teacher=paper_teacher,
                    created_by=request.user,
                )
                if components:
                    sync_exam_mark_components(
                        paper,
                        json.dumps(components),
                        skip_if_blank=False,
                    )
                created_ids.append(paper.id)
    except DJValidationError as exc:
        msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
        return JsonResponse({"ok": False, "error": msg}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "session_id": session.pk,
            "exam_ids": created_ids,
            "message": "Exam session and papers created.",
        }
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
    subjects = Subject.objects.order_by("display_order", "name")
    has_filters = role in ("ADMIN", "TEACHER")

    if school and role == "ADMIN":
        classrooms = ClassRoom.objects.select_related("academic_year").order_by(*ORDER_AY_START_GRADE_NAME)
        sections = Section.objects.order_by("name")
        teachers = (
            Teacher.objects.filter(user__school=school)
            .select_related("user")
            .order_by("user__first_name", "user__last_name")
        )
    elif role == "TEACHER":
        teacher = getattr(request.user, "teacher_profile", None)
        if teacher:
            cids = set()
            sids = set()
            for cid, sid in ClassSectionSubjectTeacher.objects.filter(teacher=teacher).values_list(
                "class_obj_id", "section_id"
            ).distinct():
                if cid:
                    cids.add(cid)
                if sid:
                    sids.add(sid)
            for classroom in teacher.classrooms.all().prefetch_related("sections"):
                cids.add(classroom.id)
                for sec in classroom.sections.all():
                    sids.add(sec.id)
            classrooms = (
                ClassRoom.objects.filter(id__in=cids).order_by(*ORDER_GRADE_NAME) if cids else ClassRoom.objects.none()
            )
            sections = Section.objects.filter(id__in=sids).order_by("name") if sids else Section.objects.none()

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
                try:
                    from django.core.exceptions import ValidationError as DJValidationError

                    from apps.core.exam_components import sync_exam_mark_components

                    with transaction.atomic():
                        session = examsession_create_compat(
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
                        if form.cleaned_data.get("use_mark_components"):
                            sync_exam_mark_components(
                                paper,
                                form.cleaned_data.get("mark_components_json") or "[]",
                                skip_if_blank=False,
                            )
                except DJValidationError as exc:
                    msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                    messages.error(request, msg)
                else:
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
    exam = get_object_or_404(
        _exam_read_qs().prefetch_related("mark_components"),
        pk=exam_id,
    )
    teacher = getattr(request.user, "teacher_profile", None) if not acting_as_admin else None

    if acting_as_admin:
        subjects = (
            Subject.objects.filter(id=exam.subject_id).order_by("display_order", "name")
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
        from .utils import teacher_allowed_class_section_pairs_lower

        if exam.class_name and exam.section:
            if (
                exam.class_name.strip().lower(),
                exam.section.strip().lower(),
            ) in teacher_allowed_class_section_pairs_lower(teacher):
                subject_id_set.update(teacher.subjects.values_list("id", flat=True))
                if teacher.subject_id:
                    subject_id_set.add(teacher.subject_id)
        if exam.subject_id:
            if exam.subject_id in subject_id_set or (exam.teacher_id and exam.teacher_id == teacher.id):
                subjects = Subject.objects.filter(id=exam.subject_id).order_by("display_order", "name")
            else:
                subjects = Subject.objects.none()
        else:
            subjects = Subject.objects.filter(id__in=subject_id_set).order_by("display_order", "name")

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
        .order_by("roll_number", "user__first_name", "user__last_name", "user__username")
    )

    # Roll number is stored as text in some setups; always sort numerically when possible.
    # Do it in Python to avoid DB-specific regex/cast behavior.
    def _roll_sort_key(s):
        raw = (getattr(s, "roll_number", "") or "").strip()
        if raw.isdigit():
            return (0, int(raw), raw)
        return (1, 10**18, raw)

    students.sort(key=_roll_sort_key)
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
        comp_rows = list(exam.mark_components.order_by("sort_order", "id"))
        has_components = bool(comp_rows)
        # Server-side validation: do not silently clamp above max.
        if has_components:
            invalid = []
            for s in students:
                for c in comp_rows:
                    raw = (request.POST.get(f"cmp_{s.id}_{c.id}") or "").strip()
                    try:
                        v = int(raw or 0)
                    except (ValueError, TypeError):
                        v = 0
                    if v < 0:
                        v = 0
                    if c.max_marks is not None and v > int(c.max_marks):
                        who = s.user.get_full_name() or s.user.username
                        invalid.append(f"{who} — {c.component_name} exceeds max {c.max_marks}.")
                        if len(invalid) >= 5:
                            break
                if len(invalid) >= 5:
                    break
            if invalid:
                messages.error(request, "Fix component marks: " + " ".join(invalid))
                return redirect(f"{enter_marks_url}?subject={subject.id}")

        with transaction.atomic():
            existing = {
                (m.student_id, m.subject_id): m
                for m in Marks.objects.filter(exam=exam, subject=subject)
            }
            to_create = []
            to_update = []
            default_tm = exam.total_marks if getattr(exam, "total_marks", None) else 100
            for s in students:
                comp_map = {}
                if has_components:
                    # Per-component entry; total is derived.
                    obtained = 0
                    for c in comp_rows:
                        key = f"cmp_{s.id}_{c.id}"
                        raw = (request.POST.get(key) or "").strip()
                        try:
                            v = int(raw or 0)
                        except (ValueError, TypeError):
                            v = 0
                        if v < 0:
                            v = 0
                        comp_map[c.component_name] = v
                        obtained += v
                    total = int(default_tm)
                else:
                    try:
                        obtained = int(request.POST.get(f"obtained_{s.id}", 0) or 0)
                        total = int(request.POST.get(f"total_{s.id}", default_tm) or default_tm)
                    except (ValueError, TypeError):
                        obtained = 0
                        total = default_tm
                    if obtained < 0:
                        obtained = 0
                    if total <= 0:
                        total = default_tm
                key = (s.id, subject.id)
                if key in existing:
                    rec = existing[key]
                    changed = rec.marks_obtained != obtained or rec.total_marks != total
                    if has_components and (rec.component_marks or {}) != comp_map:
                        changed = True
                    if changed:
                        rec.marks_obtained = obtained
                        rec.total_marks = total
                        if has_components:
                            rec.component_marks = comp_map
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
                            component_marks=(comp_map if has_components else {}),
                            entered_by=request.user,
                        )
                    )
            if to_create:
                Marks.objects.bulk_create(to_create)
            if to_update:
                Marks.objects.bulk_update(to_update, ["marks_obtained", "total_marks", "component_marks", "entered_by"])
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
            existing_marks[m.student_id] = {
                "obtained": m.marks_obtained,
                "total": m.total_marks,
                "components": (m.component_marks or {}),
            }

    default_total = exam.total_marks if getattr(exam, "total_marks", None) else 100
    students_with_marks = []
    for s in students:
        em = existing_marks.get(s.id)
        has_mark = em is not None
        if not em:
            em = {"obtained": "", "total": default_total, "components": {}}
        students_with_marks.append({
            "student": s,
            "obtained": em["obtained"],
            "total": em["total"],
            "components": em.get("components") or {},
            "has_mark": has_mark,
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
            "exam_mark_components": list(exam.mark_components.order_by("sort_order", "id")),
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
    subjects = Subject.objects.all().order_by("display_order", "name")
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
    from apps.school_data.classroom_ordering import grade_order_from_name

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
    for c in ClassRoom.objects.prefetch_related("sections").order_by(*ORDER_GRADE_NAME):
        choices_class.add((c.name, c.name))
        for sec in c.sections.all():
            choices_section.add((sec.name, sec.name))
    return sorted(choices_class, key=lambda x: (grade_order_from_name(x[0]), x[0].lower())), sorted(
        choices_section
    )


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

        from apps.school_data.calendar_policy import academic_year_for_date, resolve_day

        ay_att = academic_year_for_date(att_date)
        day_res = resolve_day(att_date, "student", ay=ay_att)
        if not day_res.is_working_day:
            messages.error(
                request,
                f"{att_date.strftime('%d %b %Y')}: {day_res.label}. Attendance is not required on this day.",
            )
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

    from apps.school_data.calendar_policy import academic_year_for_date, resolve_day

    ay_for_att = academic_year_for_date(att_date)
    day_calendar = resolve_day(att_date, "student", ay=ay_for_att)
    attendance_day_blocked = not day_calendar.is_working_day

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
        "day_calendar": day_calendar,
        "attendance_day_blocked": attendance_day_blocked,
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

            att_date = form.cleaned_data.get("date")
            from apps.school_data.calendar_policy import academic_year_for_date, resolve_day

            ay_att = academic_year_for_date(att_date)
            day_res = resolve_day(att_date, "student", ay=ay_att)
            if not day_res.is_working_day:
                messages.error(
                    request,
                    f"{att_date.strftime('%d %b %Y')}: {day_res.label}. Attendance is not required on this day.",
                )
                return redirect("core:mark_attendance")

            att = form.save(commit=False)
            att.marked_by = request.user
            att.save()
            return redirect("core:teacher_dashboard")
    else:
        form = AttendanceForm(initial={"date": timezone.localdate()})
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


@admin_required
@feature_required("fees")
def school_fee_collect_redirect(request, fee_id):
    """Legacy /school/fees/collect/<fee_id>/ → new per-student collect screen."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    fee = get_object_or_404(Fee.objects.select_related("student"), pk=fee_id)
    target = reverse("core:billing_student_collect", args=[fee.student_id])
    if fee.academic_year_id:
        target = f"{target}?ay={fee.academic_year_id}"
    return redirect(target)


def redirect_billing_dashboard(request, *args, **kwargs):
    """Ignore path kwargs (e.g. legacy fee_id / payment_id) and send users to the billing hub."""
    return redirect("core:billing_dashboard")


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


@admin_required
def school_fee_types(request):
    """Legacy URL — fee types live under Fee categories."""
    return redirect("core:billing_fee_categories")


@admin_required
@require_POST
def school_fee_type_update(request, pk):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import FeeTypeForm

    ft = get_object_or_404(FeeType, pk=pk)
    form = FeeTypeForm(request.POST, instance=ft)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.save_with_audit(request.user)
        messages.success(request, f"Fee type “{obj.name}” was updated.")
    else:
        messages.error(request, "Could not save fee type. Check the fields and try again.")
    return redirect("core:billing_fee_categories")


@admin_required
@require_POST
def school_fee_type_delete(request, pk):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    ft = get_object_or_404(FeeType, pk=pk)
    if ft.structures.exists():
        messages.error(
            request,
            f"Cannot delete “{ft.name}”: it is used in fee structures. Remove or reassign those mappings first.",
        )
    else:
        name = ft.name
        ft.delete()
        messages.success(request, f"Fee type “{name}” was deleted.")
    return redirect("core:billing_fee_categories")


@admin_required
def school_fee_structure(request):
    """Legacy URL — class fee structure hub."""
    return redirect("core:billing_fee_structure")


@admin_required
@require_POST
def school_fee_structure_apply(request, structure_id):
    """Create Fee dues for all students in the structure's class (optional section)."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    structure = get_object_or_404(FeeStructure, pk=structure_id)
    due_str = (request.POST.get("due_date") or "").strip()
    section_raw = (request.POST.get("section_id") or "").strip()
    section_id = int(section_raw) if section_raw.isdigit() else None
    if not due_str:
        messages.error(request, "Choose a due date before applying the fee structure.")
        return redirect("core:billing_dashboard")
    try:
        due_date = date.fromisoformat(due_str)
    except ValueError:
        messages.error(request, "Invalid due date.")
        return redirect("core:billing_dashboard")
    from . import fee_services

    n, err = fee_services.apply_structure_to_students(structure, due_date, section_id=section_id)
    if err:
        messages.error(request, err)
    else:
        messages.success(request, f"Applied: {n} new fee due(s) created for students in this class.")
    return redirect("core:billing_dashboard")


# ======================
# Parent Portal (Basic Plan)
# ======================


@parent_required
def parent_dashboard(request):
    from apps.school_data.calendar_policy import portal_holiday_widget_context

    parent = getattr(request.user, "parent_profile", None)
    if not parent:
        return render(
            request,
            "core/parent/dashboard.html",
            {"children": [], **portal_holiday_widget_context("student")},
        )
    children = list(
        Student.objects.filter(guardians__parent=parent)
        .select_related("user", "classroom", "section")
    )
    return render(
        request,
        "core/parent/dashboard.html",
        {"children": children, **portal_holiday_widget_context("student")},
    )


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
    hw = list(
        Homework.objects.filter(id__in=hw_legacy_ids)
        .defer("attachment")
        .prefetch_related("classes", "sections")
        .select_related("subject")
        .order_by("-due_date")[:20]
    )
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
    from apps.school_data.calendar_policy import academic_year_for_date, resolve_day

    total_working_days = 0
    d = start_date
    while d <= end_date:
        ay_d = academic_year_for_date(d)
        if resolve_day(d, "teacher", ay=ay_d).is_working_day:
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

    from apps.school_data.calendar_policy import academic_year_for_date, resolve_day

    leading_blanks = (first_day.weekday() + 1) % 7
    cells = [{"is_blank": True} for _ in range(leading_blanks)]
    for day_num in range(1, last_day_num + 1):
        cur = date(year, month, day_num)
        rec = by_date.get(cur)
        is_future = cur > today
        ay_cur = academic_year_for_date(cur)
        pol = resolve_day(cur, "teacher", ay=ay_cur)
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
        elif not pol.is_working_day:
            css, label, remarks = "weekend", pol.label, pol.detail or ""
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
        ay_cur = academic_year_for_date(cur)
        if cur <= today and resolve_day(cur, "teacher", ay=ay_cur).is_working_day:
            working_days += 1
        rec = by_date.get(cur)
        if rec:
            key = {"PRESENT": "present", "ABSENT": "absent", "LEAVE": "leave",
                   "HALF_DAY": "half_day", "HOLIDAY": "holiday"}.get(rec.status, "other")
            summary[key] = summary.get(key, 0) + 1

    marked_days = sum(summary.values())
    not_marked_days = max(0, working_days - marked_days)

    return render(request, "core/staff_attendance/detail.html", {
        "teacher": teacher,
        "calendar_cells": cells,
        "calendar_month_label": first_day.strftime("%B %Y"),
        "prev_month": prev_month,
        "next_month": next_month,
        "summary": summary,
        "working_days": working_days,
        "not_marked_days": not_marked_days,
        "today": today,
    })


@admin_required
def school_staff_attendance_mark(request):
    """Mark staff attendance for a single date."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    teachers = Teacher.objects.filter(user__school=school).select_related("user").order_by("user__first_name", "user__last_name")
    att_date_str = request.POST.get("date") or request.GET.get("date", timezone.localdate().isoformat())
    try:
        att_date = date.fromisoformat(att_date_str)
    except (ValueError, TypeError):
        att_date = timezone.localdate()
        att_date_str = att_date.isoformat()

    from apps.school_data.calendar_policy import academic_year_for_date, resolve_day

    ay_att = academic_year_for_date(att_date)
    day_calendar = resolve_day(att_date, "teacher", ay=ay_att)
    attendance_day_blocked = not day_calendar.is_working_day

    records = StaffAttendance.objects.filter(
        teacher__user__school=school,
        date=att_date,
    ).select_related("teacher")
    by_teacher = {r.teacher_id: r for r in records}
    if request.method == "POST":
        if attendance_day_blocked:
            messages.error(
                request,
                f"{att_date.strftime('%d %b %Y')}: {day_calendar.label}. Staff attendance is not required on this day.",
            )
            return redirect(f"{reverse('core:school_staff_attendance_mark')}?date={att_date_str}")
        valid_statuses = {s[0] for s in STATUS_CHOICES}
        try:
            with transaction.atomic():
                for t in teachers:
                    key = f"status_{t.id}"
                    if key in request.POST:
                        status = request.POST[key]
                        if status in valid_statuses:
                            StaffAttendance.objects.update_or_create(
                                teacher=t,
                                date=att_date,
                                defaults={
                                    "status": status,
                                    "remarks": (request.POST.get(f"remarks_{t.id}") or "").strip()[:200],
                                    "marked_by": request.user,
                                },
                            )
        except Exception:
            logger.exception("Staff attendance save failed")
            return redirect(f"{reverse('core:school_staff_attendance_mark')}?date={att_date_str}&error=1")
        else:
            return redirect(f"{reverse('core:school_staff_attendance_mark')}?date={att_date_str}&saved=1")
    staff_rows = []
    for t in teachers:
        rec = by_teacher.get(t.id)
        staff_rows.append(
            {
                "teacher": t,
                "current_status": rec.status if rec else "PRESENT",
                "remarks": (rec.remarks or "") if rec else "",
                "is_marked": bool(rec),
            }
        )
    return render(request, "core/staff_attendance/mark.html", {
        "staff_rows": staff_rows,
        "att_date": att_date_str,
        "status_choices": STATUS_CHOICES,
        "day_calendar": day_calendar,
        "attendance_day_blocked": attendance_day_blocked,
    })


@login_required
@feature_required("attendance")
def school_student_attendance(request):
    """
    Student attendance summary dashboard.
    - Admin: all students (filterable)
    - Teacher: only assigned class/section students
    """
    role = getattr(request.user, "role", None)
    if role not in (User.Roles.ADMIN, User.Roles.TEACHER, "ADMIN", "TEACHER"):
        return HttpResponseForbidden("Access denied.")

    # Filters
    raw_day = (request.GET.get("date") or "").strip()
    day = None
    if raw_day:
        try:
            day = date.fromisoformat(raw_day)
        except Exception:
            day = None

    def _parse_int(v):
        try:
            return int(v)
        except Exception:
            return None

    classroom_id = _parse_int(request.GET.get("classroom_id"))
    section_id = _parse_int(request.GET.get("section_id"))
    month = (request.GET.get("month") or "").strip() or None
    start_date = None
    end_date = None
    try:
        sd = (request.GET.get("start_date") or "").strip()
        ed = (request.GET.get("end_date") or "").strip()
        if sd:
            start_date = date.fromisoformat(sd)
        if ed:
            end_date = date.fromisoformat(ed)
    except Exception:
        start_date, end_date = None, None

    summary = get_student_attendance_summary(
        request.user,
        day=day,
        classroom_id=classroom_id,
        section_id=section_id,
        month=month,
        start_date=start_date,
        end_date=end_date,
    )

    export = (request.GET.get("export") or "").lower().strip()
    if export == "csv" and summary.get("is_admin"):
        rows = [["Student", "Admission No", "Class", "Section", "Status", "Marked By", "Time", "Remarks"]]
        for r in summary["table_rows"]:
            s = r["student"]
            rows.append(
                [
                    s.user.get_full_name() or s.user.username,
                    s.admission_number or "",
                    getattr(s.classroom, "name", "") if s.classroom_id else "",
                    getattr(s.section, "name", "") if s.section_id else "",
                    r["status"] or "NOT_MARKED",
                    (r["marked_by"].get_full_name() if r.get("marked_by") else "") if r.get("marked_by") else "",
                    r.get("time") or "",
                    r.get("remarks") or "",
                ]
            )
        import csv
        from io import StringIO

        buf = StringIO()
        w = csv.writer(buf)
        w.writerows(rows)
        resp = HttpResponse(buf.getvalue(), content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="student_attendance_{(summary.get("day") or date.today()).isoformat()}.csv"'
        return resp

    return render(request, "core/student_attendance/index.html", summary)


# ======================
# Holiday calendar & working-day policy
# ======================


@login_required
@feature_required("attendance")
def school_calendar_holidays(request):
    """Holiday calendar: school admins manage; teachers, students, and parents view (read-only)."""
    role = getattr(request.user, "role", None)
    if role == User.Roles.SUPERADMIN:
        return HttpResponseForbidden("Sign in as a school user to view this calendar.")
    if role not in (
        User.Roles.ADMIN,
        User.Roles.TEACHER,
        User.Roles.STUDENT,
        User.Roles.PARENT,
    ):
        return HttpResponseForbidden("This calendar is not available for your role.")

    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    if not has_feature_access(school, "attendance", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")

    can_manage = role == User.Roles.ADMIN
    calendar_dashboard_url = {
        User.Roles.ADMIN: "core:admin_dashboard",
        User.Roles.TEACHER: "core:teacher_dashboard",
        User.Roles.STUDENT: "core:student_dashboard",
        User.Roles.PARENT: "core:parent_dashboard",
    }[role]

    from apps.school_data.calendar_policy import (
        build_month_cells,
        ensure_calendar_for_academic_year,
        publish_calendar,
        unpublish_calendar,
    )

    def _parse_int(val):
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    years_qs = AcademicYear.objects.order_by("-start_date")
    ay_id = _parse_int(request.GET.get("ay") or request.POST.get("ay"))
    active_ay = get_active_academic_year_obj()
    ay = years_qs.filter(pk=ay_id).first() if ay_id else (active_ay or years_qs.first())
    if not ay:
        messages.warning(request, "Create an academic year before the school calendar is available.")
        if can_manage:
            return redirect("core:school_academic_years")
        return redirect(calendar_dashboard_url)

    try:
        cal = ensure_calendar_for_academic_year(ay)
    except ProgrammingError:
        messages.error(
            request,
            "The school calendar tables are missing on this database. From the project root, run: "
            "python manage.py ensure_holiday_calendar_tables",
        )
        return redirect(calendar_dashboard_url)

    today = date.today()

    year = _parse_int(request.GET.get("year")) or today.year
    month = _parse_int(request.GET.get("month")) or today.month
    view_mode = (request.GET.get("view") or "month").strip().lower()
    if view_mode not in ("month", "year"):
        view_mode = "month"
    if month < 1:
        month = 1
    if month > 12:
        month = 12
    # Students and parents always see the student calendar; teachers always see the staff calendar.
    # Only school admins may switch audience (or override via query string).
    if can_manage:
        raw_aud = request.GET.get("audience")
        if raw_aud is not None:
            audience = (raw_aud or "").strip().lower()
            if audience not in ("student", "teacher"):
                audience = "student"
        else:
            audience = "student"
    else:
        audience = "teacher" if role == User.Roles.TEACHER else "student"

    edit_event_id = _parse_int(request.GET.get("edit_event")) if can_manage else None
    edit_event = None
    if edit_event_id:
        edit_event = HolidayEvent.objects.filter(pk=edit_event_id, calendar=cal).first()

    event_form = None
    sunday_form = None

    if request.method == "POST" and not can_manage:
        return HttpResponseForbidden("Only school admins can change the holiday calendar.")

    if request.method == "POST":
        py = _parse_int(request.POST.get("planner_year"))
        pm = _parse_int(request.POST.get("planner_month"))
        if py:
            year = py
        if pm and 1 <= pm <= 12:
            month = pm
        if can_manage:
            pa = (request.POST.get("planner_audience") or "").strip().lower()
            if pa in ("student", "teacher"):
                audience = pa

        def _hol_redirect():
            view_mode_post = (request.POST.get("planner_view") or "").strip().lower()
            if view_mode_post in ("month", "year"):
                nonlocal view_mode
                view_mode = view_mode_post
            return redirect(
                f"{reverse('core:school_calendar_holidays')}?{urlencode({'ay': ay.id, 'year': year, 'month': month, 'audience': audience, 'view': view_mode})}"
            )

        form_type = (request.POST.get("form_type") or "").strip()
        if form_type == "publish":
            publish_calendar(cal, user=request.user)
            messages.success(request, f"Holiday calendar for {ay.name} is now published.")
            return _hol_redirect()
        if form_type == "unpublish":
            unpublish_calendar(cal, user=request.user)
            messages.success(
                request,
                f"Holiday calendar for {ay.name} is unpublished (only the default Sunday rule applies).",
            )
            return _hol_redirect()
        if form_type == "split_toggle":
            cal.use_split_calendars = request.POST.get("use_split_calendars") == "1"
            cal.save_with_audit(request.user)
            messages.success(request, "Calendar display preference updated.")
            return _hol_redirect()
        if form_type == "holiday_event":
            edit_id = _parse_int(request.POST.get("event_id"))
            instance = HolidayEvent.objects.filter(pk=edit_id, calendar=cal).first() if edit_id else None
            bound = HolidayEventForm(request.POST, instance=instance, academic_year=ay)
            if bound.is_valid():
                obj = bound.save(commit=False)
                obj.calendar = cal
                obj.save_with_audit(request.user)
                messages.success(request, "Holiday saved.")
                return _hol_redirect()
            messages.error(request, "Please correct the errors below.")
            event_form = bound
        elif form_type == "delete_event":
            eid = _parse_int(request.POST.get("event_id"))
            if eid:
                HolidayEvent.objects.filter(pk=eid, calendar=cal).delete()
                messages.success(request, "Holiday removed.")
            return _hol_redirect()
        elif form_type == "working_sunday":
            bound = WorkingSundayOverrideForm(request.POST)
            if bound.is_valid():
                obj = bound.save(commit=False)
                obj.calendar = cal
                try:
                    obj.save_with_audit(request.user)
                except IntegrityError:
                    messages.error(request, "That Sunday and audience already has an override.")
                    sunday_form = bound
                else:
                    messages.success(request, "Working Sunday saved.")
                    return _hol_redirect()
            else:
                messages.error(request, "Please correct the working Sunday form.")
                sunday_form = bound
        elif form_type == "delete_working_sunday":
            wid = _parse_int(request.POST.get("override_id"))
            if wid:
                WorkingSundayOverride.objects.filter(pk=wid, calendar=cal).delete()
                messages.success(request, "Working Sunday override removed.")
            return _hol_redirect()

    if can_manage:
        if event_form is None:
            event_form = HolidayEventForm(
                instance=edit_event,
                initial={"calendar": cal},
                academic_year=ay,
            )
            if not edit_event:
                event_form.fields["calendar"].initial = cal.pk
        if sunday_form is None:
            sunday_form = WorkingSundayOverrideForm(initial={"calendar": cal.pk})

    events = list(HolidayEvent.objects.filter(calendar=cal).order_by("start_date", "name"))
    overrides = list(cal.working_sunday_overrides.order_by("work_date"))

    month_cells_student = build_month_cells(year, month, cal, audience="student")
    month_cells_teacher = build_month_cells(year, month, cal, audience="teacher")
    month_cells = month_cells_teacher if audience == "teacher" else month_cells_student

    year_months_student = []
    year_months_teacher = []
    if view_mode == "year":
        for m in range(1, 13):
            year_months_student.append(
                {"month": m, "label": date(year, m, 1).strftime("%b"), "cells": build_month_cells(year, m, cal, audience="student")}
            )
            year_months_teacher.append(
                {"month": m, "label": date(year, m, 1).strftime("%b"), "cells": build_month_cells(year, m, cal, audience="teacher")}
            )
    year_months = year_months_teacher if audience == "teacher" else year_months_student

    prev_m, prev_y = (month - 1, year) if month > 1 else (12, year - 1)
    next_m, next_y = (month + 1, year) if month < 12 else (1, year + 1)

    return render(
        request,
        "core/school/calendar/holidays.html",
        {
            "academic_years": years_qs,
            "ay": ay,
            "cal": cal,
            "events": events,
            "overrides": overrides,
            "event_form": event_form,
            "sunday_form": sunday_form,
            "edit_event": edit_event,
            "planner_year": year,
            "planner_month": month,
            "planner_month_label": date(year, month, 1).strftime("%B %Y"),
            "month_cells": month_cells,
            "month_cells_student": month_cells_student,
            "month_cells_teacher": month_cells_teacher,
            "view_mode": view_mode,
            "year_months": year_months,
            "audience": audience,
            "prev_month": prev_m,
            "prev_year": prev_y,
            "next_month": next_m,
            "next_year": next_y,
            "holiday_cal_can_manage": can_manage,
            "calendar_dashboard_url": calendar_dashboard_url,
        },
    )


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


@login_required
def school_inventory_removed(request):
    """
    Inventory module entrypoint (removed).
    Keep a friendly screen instead of Django technical 404 for old bookmarks/links.
    """
    return render(
        request,
        "core/errors/module_removed.html",
        {
            "title": "Inventory",
            "message": "This module is not available in this ERP.",
            "back_url_name": "core:admin_dashboard",
        },
        status=410,
    )


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
    return redirect("reports:dashboard")


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
        ret_date = timezone.localdate()
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
    assignments = StudentRouteAssignment.objects.all().select_related("student__user", "route", "vehicle")
    return render(
        request,
        "core/transport/index.html",
        {
            "routes": routes,
            "vehicles": vehicles,
            "assignments": assignments,
            "routes_count": routes.count(),
            "vehicles_count": vehicles.count(),
            "assignments_count": assignments.count(),
        },
    )


@admin_required
def school_transport_routes(request):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    routes = Route.objects.all().order_by("name")
    return render(request, "core/transport/routes_list.html", {"routes": routes})


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
            return redirect("core:school_transport_routes")
    else:
        form = RouteForm()
    return render(request, "core/transport/route_form.html", {"form": form})


@admin_required
def school_transport_route_view(request, route_id):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    route = get_object_or_404(Route, pk=route_id)
    vehicles = list(Vehicle.objects.filter(route=route).order_by("registration_number"))
    assignments = list(
        StudentRouteAssignment.objects.filter(route=route).select_related("student__user", "vehicle").order_by("student__user__first_name")
    )
    return render(request, "core/transport/route_view.html", {"route": route, "vehicles": vehicles, "assignments": assignments})


@admin_required
def school_transport_route_edit(request, route_id):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import RouteForm

    route = get_object_or_404(Route, pk=route_id)
    form = RouteForm(request.POST or None, instance=route)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.save_with_audit(request.user)
        return redirect("core:school_transport_route_view", route_id=obj.id)
    return render(request, "core/transport/route_form.html", {"form": form, "route": route})


@admin_required
def school_transport_route_delete(request, route_id):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    route = get_object_or_404(Route, pk=route_id)
    if request.method == "POST":
        route.delete()
        return redirect("core:school_transport_routes")
    return redirect("core:school_transport_route_view", route_id=route.id)


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
            return redirect("core:school_transport_vehicles")
    else:
        form = VehicleForm()
        form.fields["route"].queryset = Route.objects.all()
    return render(request, "core/transport/vehicle_form.html", {"form": form})

 
@admin_required
def school_transport_vehicles(request):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    vehicles = Vehicle.objects.select_related("route").order_by("registration_number")
    return render(request, "core/transport/vehicles_list.html", {"vehicles": vehicles})


@admin_required
def school_transport_vehicle_view(request, vehicle_id):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    vehicle = get_object_or_404(Vehicle.objects.select_related("route"), pk=vehicle_id)
    assignments = list(
        StudentRouteAssignment.objects.filter(vehicle=vehicle).select_related("student__user", "route").order_by("student__user__first_name")
    )
    return render(request, "core/transport/vehicle_view.html", {"vehicle": vehicle, "assignments": assignments})


@admin_required
def school_transport_vehicle_edit(request, vehicle_id):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    from .forms import VehicleForm

    vehicle = get_object_or_404(Vehicle, pk=vehicle_id)
    form = VehicleForm(request.POST or None, instance=vehicle)
    form.fields["route"].queryset = Route.objects.all()
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.save_with_audit(request.user)
        return redirect("core:school_transport_vehicle_view", vehicle_id=obj.id)
    return render(request, "core/transport/vehicle_form.html", {"form": form, "vehicle": vehicle})


@admin_required
def school_transport_vehicle_delete(request, vehicle_id):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id)
    if request.method == "POST":
        vehicle.delete()
        return redirect("core:school_transport_vehicles")
    return redirect("core:school_transport_vehicle_view", vehicle_id=vehicle.id)


@admin_required
def school_transport_assignments(request):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    assignments = StudentRouteAssignment.objects.select_related("student__user", "route", "vehicle").order_by(
        "route__name", "student__user__first_name"
    )
    return render(request, "core/transport/assignments_list.html", {"assignments": assignments})


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
        return redirect("core:school_transport_assignments")
    routes = Route.objects.all()
    students = Student.objects.all()
    vehicles = Vehicle.objects.all()
    return render(request, "core/transport/assign.html", {"routes": routes, "students": students, "vehicles": vehicles})


@admin_required
def school_transport_assignment_view(request, assignment_id):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    assignment = get_object_or_404(
        StudentRouteAssignment.objects.select_related("student__user", "route", "vehicle"), pk=assignment_id
    )
    return render(request, "core/transport/assignment_view.html", {"assignment": assignment})


@admin_required
def school_transport_assignment_edit(request, assignment_id):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    assignment = get_object_or_404(StudentRouteAssignment.objects.select_related("student__user", "route", "vehicle"), pk=assignment_id)
    if request.method == "POST":
        route_id = request.POST.get("route_id")
        vehicle_id = request.POST.get("vehicle_id")
        pickup = request.POST.get("pickup_point", "")
        if route_id:
            try:
                assignment.route = Route.objects.get(id=route_id)
            except Route.DoesNotExist:
                pass
        assignment.vehicle = Vehicle.objects.filter(id=vehicle_id).first() if vehicle_id else None
        assignment.pickup_point = pickup or ""
        assignment.save_with_audit(request.user)
        return redirect("core:school_transport_assignment_view", assignment_id=assignment.id)
    routes = Route.objects.all()
    vehicles = Vehicle.objects.all()
    return render(request, "core/transport/assignment_form.html", {"assignment": assignment, "routes": routes, "vehicles": vehicles})


@admin_required
def school_transport_assignment_delete(request, assignment_id):
    school = _school_module_check(request, "transport")
    if not school:
        return redirect("core:admin_dashboard")
    assignment = get_object_or_404(StudentRouteAssignment, pk=assignment_id)
    if request.method == "POST":
        assignment.delete()
        return redirect("core:school_transport_assignments")
    return redirect("core:school_transport_assignment_view", assignment_id=assignment.id)


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

