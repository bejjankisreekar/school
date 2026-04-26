"""School Fees & Billing — dashboard and class fee structure."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.utils import get_active_academic_year_obj
from apps.school_data.classroom_ordering import ORDER_AY_START_GRADE_NAME
from apps.school_data.models import (
    AcademicYear,
    ClassRoom,
    Fee,
    FeeStructure,
    FeeType,
    Payment,
    PaymentBatch,
    Section,
    Student,
)

from . import fee_services
from .views import _school_fee_check, admin_required, feature_required

logger = logging.getLogger(__name__)


def _fee_structure_batch_success_message(
    classroom: ClassRoom,
    *,
    apply_all: bool,
    section_ids: list[int],
    edit_batch_key: str | None,
) -> str:
    """Single user-facing success line after batch save (toasts only; details go to logs)."""
    if apply_all or not section_ids:
        sec_part = "all sections"
    else:
        n = len(section_ids)
        sec_part = f"{n} section{'s' if n != 1 else ''}"
    verb = "updated" if (edit_batch_key and str(edit_batch_key).strip()) else "created"
    return f"Fee structure successfully {verb} for {classroom.name} ({sec_part})."


def _fee_structure_batch_success_message_with_sync(
    classroom: ClassRoom,
    *,
    apply_all: bool,
    section_ids: list[int],
    edit_batch_key: str | None,
    student_fee_rows_synced: int,
) -> str:
    base = _fee_structure_batch_success_message(
        classroom,
        apply_all=apply_all,
        section_ids=section_ids,
        edit_batch_key=edit_batch_key,
    )
    if student_fee_rows_synced > 0:
        return (
            f"{base} {student_fee_rows_synced} student fee line(s) updated to match new amounts."
        )
    return base


@admin_required
@feature_required("fees")
def billing_dashboard(request):
    """Executive billing dashboard — KPIs, charts, and fee structure overview."""
    school = _school_fee_check(request)
    if not school:
        messages.warning(request, "Fee module not available.")
        return redirect("core:admin_dashboard")

    ay = get_active_academic_year_obj()
    ay_param = (request.GET.get("ay") or "").strip()
    if ay_param:
        try:
            ay = AcademicYear.objects.get(pk=int(ay_param))
        except (ValueError, AcademicYear.DoesNotExist):
            pass

    kpis = fee_services.build_kpis(ay)
    kpis["students_pending_any"] = fee_services.count_students_with_any_pending_fee(ay)

    fee_class_summaries = fee_services.build_fee_structure_class_summaries(ay)

    bar_labels, bar_vals = fee_services.chart_monthly_collections(8)
    pie = fee_services.chart_paid_vs_pending(ay)
    class_labels, class_vals = fee_services.chart_class_revenue(ay)
    cat_labels, cat_vals = fee_services.chart_collection_by_fee_type(ay, limit=10)

    return render(
        request,
        "core/billing/dashboard.html",
        {
            "academic_year": ay,
            "academic_years": AcademicYear.objects.order_by("-start_date"),
            "kpis": kpis,
            "fee_class_summaries": fee_class_summaries,
            "chart_bar_labels": json.dumps(bar_labels),
            "chart_bar_values": json.dumps(bar_vals),
            "chart_pie_paid": pie["paid"],
            "chart_pie_pending": pie["pending"],
            "chart_class_labels": json.dumps(class_labels),
            "chart_class_values": json.dumps(class_vals),
            "chart_cat_labels": json.dumps(cat_labels),
            "chart_cat_values": json.dumps(cat_vals),
            "fee_sections": fee_services.section_class_pairs(),
        },
    )


@admin_required
@feature_required("fees")
def billing_class_financial_summary(request, classroom_id: int):
    """Class-level fee totals and student breakdown for the selected academic year."""
    school = _school_fee_check(request)
    if not school:
        messages.warning(request, "Fee module not available.")
        return redirect("core:admin_dashboard")

    classroom = get_object_or_404(ClassRoom, pk=classroom_id)
    ay = get_active_academic_year_obj()
    ay_param = (request.GET.get("ay") or "").strip()
    if ay_param:
        try:
            ay = AcademicYear.objects.get(pk=int(ay_param))
        except (ValueError, AcademicYear.DoesNotExist):
            pass

    summary = fee_services.get_class_fee_summary_row(classroom_id, ay)
    if summary is None:
        raise Http404("No fee summary for this class in the selected academic year.")

    return render(
        request,
        "core/billing/class_financial_summary.html",
        {
            "classroom": classroom,
            "academic_year": ay,
            "academic_years": AcademicYear.objects.order_by("-start_date"),
            "summary": summary,
        },
    )


@admin_required
@feature_required("fees")
def billing_structure_impacted_count(request):
    """JSON: active student count for class / optional section(s) (fee structure preview)."""
    school = _school_fee_check(request)
    if not school:
        return JsonResponse({"count": 0}, status=403)
    cid = (request.GET.get("classroom_id") or "").strip()
    if not cid.isdigit():
        return JsonResponse({"count": 0})
    classroom_id = int(cid)
    # Multi-select: section_ids=1&section_ids=2 or comma-separated section_ids=1,2,3
    raw_multi = request.GET.getlist("section_ids")
    if raw_multi:
        section_ids = []
        for chunk in raw_multi:
            for part in str(chunk).split(","):
                p = part.strip()
                if p.isdigit():
                    section_ids.append(int(p))
        n = fee_services.count_students_impacted_by_class_sections(classroom_id, section_ids or None)
        return JsonResponse({"count": n})
    sid = (request.GET.get("section_id") or "").strip()
    section_id = int(sid) if sid.isdigit() else None
    n = fee_services.count_students_impacted_by_class_section(classroom_id, section_id)
    return JsonResponse({"count": n})


@admin_required
@feature_required("fees")
def billing_fee_categories(request):
    """Fee types / categories (tuition, transport, etc.) — CRUD and usage overview."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    from .forms import FeeTypeForm

    items = (
        FeeType.objects.annotate(
            mapped_classes=Count(
                "structures__classroom",
                filter=Q(structures__classroom__isnull=False),
                distinct=True,
            ),
            structure_rows=Count("structures", distinct=True),
        )
        .prefetch_related(
            Prefetch(
                "structures",
                queryset=FeeStructure.objects.select_related(
                    "classroom", "academic_year", "section"
                ).order_by(
                    "classroom__grade_order",
                    "classroom__name",
                    "academic_year__name",
                    "fee_type__name",
                ),
            )
        )
        .order_by("name")
    )

    form_t = FeeTypeForm()
    if request.method == "POST" and request.POST.get("save_fee_type"):
        form_t = FeeTypeForm(request.POST)
        if form_t.is_valid():
            obj = form_t.save(commit=False)
            obj.save_with_audit(request.user)
            messages.success(request, f"Fee type “{obj.name}” was added.")
            return redirect("core:billing_fee_categories")
        messages.error(request, "Could not add fee type.")

    total_types = FeeType.objects.count()
    active_types = FeeType.objects.filter(is_active=True).count()
    usage_payload = {}
    for ft in items:
        usage_payload[str(ft.id)] = [
            {
                "classroom": s.classroom.name if s.classroom_id else "—",
                "year": s.academic_year.name if s.academic_year_id else "—",
                "amount": str(s.amount),
                "frequency": s.get_frequency_display(),
                "mapping_active": s.is_active,
            }
            for s in ft.structures.all()
        ]

    # Open “Add fee type” from fee-structure create via ?add=1, or after invalid POST
    _add_q = (request.GET.get("add") or "").strip().lower()
    show_add_modal = (
        request.method == "GET" and _add_q in ("1", "true", "yes", "on")
    ) or (
        request.method == "POST"
        and request.POST.get("save_fee_type")
        and not form_t.is_valid()
    )

    return render(
        request,
        "core/billing/fee_categories.html",
        {
            "form_t": form_t,
            "items": items,
            "show_add_modal": show_add_modal,
            "ft_kpis": {
                "total": total_types,
                "active": active_types,
                "inactive": total_types - active_types,
                "mapped_classes": FeeStructure.objects.filter(classroom_id__isnull=False)
                .values("classroom_id")
                .distinct()
                .count(),
            },
            "usage_payload": usage_payload,
        },
    )


@admin_required
@feature_required("fees")
def billing_fee_structure_batch_detail(request, batch_key):
    """JSON payload to pre-fill the fee structure modal (edit batch)."""
    school = _school_fee_check(request)
    if not school:
        return JsonResponse({"ok": False}, status=403)
    payload = fee_services.fee_structure_batch_edit_payload(batch_key)
    if not payload:
        return JsonResponse({"ok": False, "error": "Batch not found."}, status=404)
    return JsonResponse({"ok": True, "batch": payload})


@admin_required
@feature_required("fees")
def billing_fee_structure_batch_delete(request, batch_key):
    """Delete or deactivate all fee structure lines in a batch."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed."}, status=405)
    school = _school_fee_check(request)
    if not school:
        return JsonResponse({"ok": False, "error": "Forbidden."}, status=403)
    ok, err, soft = fee_services.delete_fee_structure_batch(request.user, batch_key)
    if not ok:
        return JsonResponse({"ok": False, "error": err or "Could not delete batch."}, status=400)
    return JsonResponse(
        {
            "ok": True,
            "soft_delete": soft,
            "message": (
                "Fee lines were deactivated because students already have fee rows linked to this batch."
                if soft
                else "Fee structure batch removed."
            ),
        }
    )


@admin_required
@feature_required("fees")
def billing_class_fee_structure(request):
    """Class-wise fee setup: structure lines, auto-assign on save."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    ay = get_active_academic_year_obj()
    ay_param = (request.GET.get("ay") or request.POST.get("ay") or "").strip()
    if ay_param:
        try:
            ay = AcademicYear.objects.get(pk=int(ay_param))
        except (ValueError, AcademicYear.DoesNotExist):
            pass

    from .forms import FeeStructureForm, FeeTypeForm

    form_s = FeeStructureForm(school)
    form_t = FeeTypeForm()
    show_add_fee_modal = False
    open_fee_line_modal = False
    if request.method == "POST":
        if request.POST.get("save_fee_type"):
            form_t = FeeTypeForm(request.POST)
            if form_t.is_valid():
                obj = form_t.save(commit=False)
                obj.save_with_audit(request.user)
                messages.success(request, f"Fee type \"{obj.name}\" was added.")
                ay_q = f"?ay={ay.id}" if ay else ""
                return redirect(reverse("core:billing_fee_structure") + ay_q)
            messages.error(request, "Could not add fee type.")
            show_add_fee_modal = True
        elif request.POST.get("save_fee_structure_batch"):
            raw_lines = (request.POST.get("fee_lines_json") or "").strip()
            try:
                fee_lines = json.loads(raw_lines) if raw_lines else []
            except json.JSONDecodeError:
                fee_lines = []
            if not isinstance(fee_lines, list):
                fee_lines = []
            section_ids = []
            for x in request.POST.getlist("section_ids"):
                for part in str(x).split(","):
                    p = part.strip()
                    if p.isdigit():
                        section_ids.append(int(p))
            apply_all = request.POST.get("apply_all_sections") == "1"
            if apply_all:
                section_ids = []
            classroom_id = (request.POST.get("classroom") or "").strip()
            ay_post = (request.POST.get("academic_year") or "").strip()
            edit_batch = (request.POST.get("edit_batch_key") or "").strip() or None
            try:
                classroom = ClassRoom.objects.get(pk=int(classroom_id)) if classroom_id.isdigit() else None
            except ClassRoom.DoesNotExist:
                classroom = None
            acad = None
            if ay_post.isdigit():
                try:
                    acad = AcademicYear.objects.get(pk=int(ay_post))
                except AcademicYear.DoesNotExist:
                    acad = None
            if not classroom:
                messages.error(request, "Select a class.")
                open_fee_line_modal = True
            else:
                from datetime import datetime as dt_module

                fdd = request.POST.get("first_due_date") or ""
                first_due = None
                if fdd:
                    try:
                        first_due = dt_module.strptime(fdd[:10], "%Y-%m-%d").date()
                    except ValueError:
                        first_due = None
                ddm = (request.POST.get("due_day_of_month") or "").strip()
                due_day = int(ddm) if ddm.isdigit() else None
                n_auto, infos, err, n_fee_sync = fee_services.save_fee_structure_batch(
                    request.user,
                    academic_year=acad,
                    classroom=classroom,
                    section_ids=section_ids,
                    fee_lines=fee_lines,
                    frequency=(request.POST.get("frequency") or "").strip() or "MONTHLY",
                    first_due_date=first_due,
                    due_day_of_month=due_day,
                    late_fine_rule=(request.POST.get("late_fine_rule") or "").strip(),
                    discount_allowed=request.POST.get("cfs_discount_allowed", "on") == "on",
                    installments_enabled=request.POST.get("cfs_installments_enabled") == "on",
                    is_active=request.POST.get("cfs_is_active", "on") == "on",
                    edit_batch_key=edit_batch,
                )
                if err:
                    messages.error(request, err)
                    open_fee_line_modal = True
                else:
                    if infos:
                        logger.debug("save_fee_structure_batch infos: %s", " | ".join(infos))
                    if n_auto:
                        logger.debug(
                            "save_fee_structure_batch: auto-assigned %s student fee row(s)",
                            n_auto,
                        )
                    if n_fee_sync:
                        logger.debug(
                            "save_fee_structure_batch: synced %s student fee line(s) to new amounts",
                            n_fee_sync,
                        )
                    messages.success(
                        request,
                        _fee_structure_batch_success_message_with_sync(
                            classroom,
                            apply_all=apply_all,
                            section_ids=section_ids,
                            edit_batch_key=edit_batch,
                            student_fee_rows_synced=n_fee_sync,
                        ),
                    )
                    return redirect(reverse("core:billing_fee_structure") + (f"?ay={ay.id}" if ay else ""))
        elif request.POST.get("save_fee_structure"):
            form_s = FeeStructureForm(school, request.POST)
            if form_s.is_valid():
                obj = form_s.save(commit=False)
                obj.save_with_audit(request.user)
                n_sync = fee_services.sync_student_fees_to_fee_structure(obj, request.user)
                n_auto, err_auto = fee_services.auto_assign_fees_for_structure(obj)
                if err_auto:
                    messages.warning(request, err_auto)
                else:
                    who = obj.classroom.name if obj.classroom_id else "this fee line"
                    msg = f"Fee structure line saved for {who}."
                    if n_sync:
                        msg += f" {n_sync} student fee line(s) updated to match new amounts."
                    messages.success(request, msg)
                    if n_auto:
                        logger.debug(
                            "auto_assign_fees_for_structure: %s new student fee row(s) for structure %s",
                            n_auto,
                            obj.pk,
                        )
                return redirect(reverse("core:billing_fee_structure") + (f"?ay={ay.id}" if ay else ""))
            messages.error(request, "Check structure fields and try again.")
            open_fee_line_modal = True

    class_cards = fee_services.build_class_fee_structure_cards(ay)
    fee_categories = list(
        FeeType.objects.filter(is_active=True).order_by("name").values("id", "name", "code")
    )
    fee_types_for_ui = list(
        FeeType.objects.order_by("name").values("id", "name", "code", "is_active")
    )

    ay_q = f"?ay={ay.id}" if ay else ""
    return render(
        request,
        "core/billing/class_fee_structure.html",
        {
            "form_s": form_s,
            "form_t": form_t,
            "show_add_fee_modal": show_add_fee_modal,
            "impacted_count_url": reverse("core:billing_structure_impacted_count"),
            "all_classrooms": ClassRoom.objects.prefetch_related("sections").all().order_by(*ORDER_AY_START_GRADE_NAME),
            "all_sections": Section.objects.prefetch_related("classrooms").order_by("name"),
            "academic_year": ay,
            "academic_years": AcademicYear.objects.order_by("-start_date"),
            "class_cards": class_cards,
            "open_fee_line_modal": open_fee_line_modal,
            "fee_categories": fee_categories,
            "fee_types_json": json.dumps(fee_types_for_ui),
            "fee_structure_form_action": reverse("core:billing_fee_structure") + ay_q,
            "fee_structure_list_url": reverse("core:billing_fee_structure") + ay_q,
            "fee_structure_ui_meta": fee_services.fee_structure_ui_meta_for_billing(),
        },
    )


@admin_required
@feature_required("fees")
def billing_fee_structure_create(request):
    """Full-page form to create a class fee structure batch (replaces modal create flow)."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    ay = get_active_academic_year_obj()
    ay_param = (request.GET.get("ay") or request.POST.get("ay") or "").strip()
    if ay_param:
        try:
            ay = AcademicYear.objects.get(pk=int(ay_param))
        except (ValueError, AcademicYear.DoesNotExist):
            pass

    from .forms import FeeStructureForm, FeeTypeForm

    form_s = FeeStructureForm(school)
    form_t = FeeTypeForm()
    show_add_fee_modal = False

    if request.method == "POST" and request.POST.get("save_fee_type"):
        form_t = FeeTypeForm(request.POST)
        if form_t.is_valid():
            obj = form_t.save(commit=False)
            obj.save_with_audit(request.user)
            messages.success(request, f"Fee type \"{obj.name}\" was added.")
            ay_q = f"?ay={ay.id}" if ay else ""
            return redirect(reverse("core:billing_fee_structure_create") + ay_q)
        messages.error(request, "Could not add fee type.")
        show_add_fee_modal = True

    if request.method == "POST" and request.POST.get("save_fee_structure_batch"):
        raw_lines = (request.POST.get("fee_lines_json") or "").strip()
        try:
            fee_lines = json.loads(raw_lines) if raw_lines else []
        except json.JSONDecodeError:
            fee_lines = []
        if not isinstance(fee_lines, list):
            fee_lines = []
        section_ids = []
        for x in request.POST.getlist("section_ids"):
            for part in str(x).split(","):
                p = part.strip()
                if p.isdigit():
                    section_ids.append(int(p))
        apply_all = request.POST.get("apply_all_sections") == "1"
        if apply_all:
            section_ids = []
        classroom_id = (request.POST.get("classroom") or "").strip()
        ay_post = (request.POST.get("academic_year") or "").strip()
        edit_batch = (request.POST.get("edit_batch_key") or "").strip() or None
        try:
            classroom = ClassRoom.objects.get(pk=int(classroom_id)) if classroom_id.isdigit() else None
        except ClassRoom.DoesNotExist:
            classroom = None
        acad = None
        if ay_post.isdigit():
            try:
                acad = AcademicYear.objects.get(pk=int(ay_post))
            except AcademicYear.DoesNotExist:
                acad = None
        if not classroom:
            messages.error(request, "Select a class.")
        else:
            from datetime import datetime as dt_module

            fdd = request.POST.get("first_due_date") or ""
            first_due = None
            if fdd:
                try:
                    first_due = dt_module.strptime(fdd[:10], "%Y-%m-%d").date()
                except ValueError:
                    first_due = None
            ddm = (request.POST.get("due_day_of_month") or "").strip()
            due_day = int(ddm) if ddm.isdigit() else None
            n_auto, infos, err, n_fee_sync = fee_services.save_fee_structure_batch(
                request.user,
                academic_year=acad,
                classroom=classroom,
                section_ids=section_ids,
                fee_lines=fee_lines,
                frequency=(request.POST.get("frequency") or "").strip() or "MONTHLY",
                first_due_date=first_due,
                due_day_of_month=due_day,
                late_fine_rule=(request.POST.get("late_fine_rule") or "").strip(),
                discount_allowed=request.POST.get("cfs_discount_allowed", "on") == "on",
                installments_enabled=request.POST.get("cfs_installments_enabled") == "on",
                is_active=request.POST.get("cfs_is_active", "on") == "on",
                edit_batch_key=edit_batch,
            )
            if err:
                messages.error(request, err)
            else:
                if infos:
                    logger.debug("save_fee_structure_batch infos: %s", " | ".join(infos))
                if n_auto:
                    logger.debug(
                        "save_fee_structure_batch: auto-assigned %s student fee row(s)",
                        n_auto,
                    )
                if n_fee_sync:
                    logger.debug(
                        "save_fee_structure_batch: synced %s student fee line(s) to new amounts",
                        n_fee_sync,
                    )
                messages.success(
                    request,
                    _fee_structure_batch_success_message_with_sync(
                        classroom,
                        apply_all=apply_all,
                        section_ids=section_ids,
                        edit_batch_key=edit_batch,
                        student_fee_rows_synced=n_fee_sync,
                    ),
                )
                return redirect(reverse("core:billing_fee_structure") + (f"?ay={ay.id}" if ay else ""))

    fee_categories = list(
        FeeType.objects.filter(is_active=True).order_by("name").values("id", "name", "code")
    )
    fee_types_for_ui = list(
        FeeType.objects.order_by("name").values("id", "name", "code", "is_active")
    )
    ay_q = f"?ay={ay.id}" if ay else ""
    return render(
        request,
        "core/billing/fee_structure_create.html",
        {
            "form_s": form_s,
            "form_t": form_t,
            "show_add_fee_modal": show_add_fee_modal,
            "impacted_count_url": reverse("core:billing_structure_impacted_count"),
            "all_classrooms": ClassRoom.objects.prefetch_related("sections").all().order_by(*ORDER_AY_START_GRADE_NAME),
            "all_sections": Section.objects.prefetch_related("classrooms").order_by("name"),
            "academic_year": ay,
            "academic_years": AcademicYear.objects.order_by("-start_date"),
            "fee_categories": fee_categories,
            "fee_types_json": json.dumps(fee_types_for_ui),
            "fee_structure_page": True,
            "fee_structure_form_action": reverse("core:billing_fee_structure_create") + ay_q,
            "fee_structure_list_url": reverse("core:billing_fee_structure") + ay_q,
            "open_fee_line_modal": False,
            "fee_structure_ui_meta": fee_services.fee_structure_ui_meta_for_billing(),
        },
    )


@admin_required
@feature_required("fees")
def billing_class_fee_students(request, classroom_id: int):
    """Students in a class with fee totals, concessions, paid and pending (for selected year)."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    classroom = get_object_or_404(ClassRoom, pk=classroom_id)
    ay = get_active_academic_year_obj()
    ay_param = (request.GET.get("ay") or "").strip()
    if ay_param:
        try:
            ay = AcademicYear.objects.get(pk=int(ay_param))
        except (ValueError, AcademicYear.DoesNotExist):
            pass

    section_id = None
    if (s := (request.GET.get("section_id") or "").strip()).isdigit():
        section_id = int(s)

    rows = fee_services.build_classroom_student_fee_rollups(classroom_id, ay)
    if section_id:
        rows = [r for r in rows if getattr(r["student"], "section_id", None) == section_id]
    # Class-level totals for KPI cards.
    class_totals = {
        "students": len(rows),
        "gross": sum((r.get("gross") or Decimal("0") for r in rows), Decimal("0")),
        "concession": sum((r.get("concession") or Decimal("0") for r in rows), Decimal("0")),
        "net_due": sum((r.get("net_due") or Decimal("0") for r in rows), Decimal("0")),
        "paid": sum((r.get("paid") or Decimal("0") for r in rows), Decimal("0")),
        "pending": sum((r.get("pending") or Decimal("0") for r in rows), Decimal("0")),
    }
    card_lines = None
    card_total = None
    for c in fee_services.build_class_fee_structure_cards(ay):
        if c["classroom_id"] == classroom_id:
            card_lines = c["lines"]
            card_total = c["structure_total"]
            break

    return render(
        request,
        "core/billing/class_fee_students.html",
        {
            "classroom": classroom,
            "academic_year": ay,
            "academic_years": AcademicYear.objects.order_by("-start_date"),
            "sections": list(classroom.sections.all().order_by("name")),
            "section_id": section_id,
            "rows": rows,
            "class_totals": class_totals,
            "structure_lines": card_lines,
            "structure_total": card_total,
        },
    )


@admin_required
@feature_required("fees")
def billing_student_fee_lines(request, classroom_id: int, student_id: int):
    """Edit assigned fee lines for one student: billed amount, due date, concessions."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    from .forms import FeeConcessionForm, FeeLineAdjustForm

    classroom = get_object_or_404(ClassRoom, pk=classroom_id)
    student = get_object_or_404(
        Student.objects.select_related("user", "section", "classroom"),
        pk=student_id,
        classroom_id=classroom_id,
    )

    ay = get_active_academic_year_obj()
    ay_param = (request.GET.get("ay") or "").strip()
    if ay_param:
        try:
            ay = AcademicYear.objects.get(pk=int(ay_param))
        except (ValueError, AcademicYear.DoesNotExist):
            pass

    def redirect_back():
        q = {}
        if ay:
            q["ay"] = ay.id
        target = reverse("core:billing_student_fee_lines", args=[classroom_id, student_id])
        if q:
            target = f"{target}?{urlencode(q)}"
        return redirect(target)

    error_fee_id = None
    error_fee_tab = None  # "amt" | "conc" | "del" — which tab to show after validation error
    error_adjust_form = None
    error_concession_form = None

    if request.method == "POST":
        if (p := (request.POST.get("ay") or "").strip()).isdigit():
            try:
                ay = AcademicYear.objects.get(pk=int(p))
            except AcademicYear.DoesNotExist:
                pass

        fee = get_object_or_404(
            Fee.objects.select_related("fee_structure", "student"),
            pk=int(request.POST.get("fee_id") or 0),
        )
        if fee.student_id != student.pk:
            messages.error(request, "That fee line does not belong to this student.")
            return redirect_back()

        if request.POST.get("save_fee_line"):
            form = FeeLineAdjustForm(request.POST, instance=fee, prefix=f"adj{fee.id}")
            if form.is_valid():
                form.save()
                fee_services.refresh_fee_status_from_payments(fee)
                messages.success(request, "Fee line updated.")
                return redirect_back()
            error_fee_id = fee.id
            error_fee_tab = "amt"
            error_adjust_form = form
            messages.error(request, "Check billed amount and due date.")
        elif request.POST.get("save_concession"):
            if not fee.fee_structure.discount_allowed:
                messages.error(
                    request,
                    "Concessions are turned off for this fee head. Enable “Discount allowed” on the structure line.",
                )
                error_fee_id = fee.id
                error_fee_tab = "conc"
            else:
                form = FeeConcessionForm(request.POST, instance=fee, prefix=f"conc{fee.id}")
                if form.is_valid():
                    form.save()
                    fee_services.refresh_fee_status_from_payments(fee)
                    messages.success(request, "Concession saved.")
                    return redirect_back()
                error_fee_id = fee.id
                error_fee_tab = "conc"
                error_concession_form = form
                messages.error(request, "Check the concession values.")
        elif request.POST.get("delete_fee_line"):
            paid = fee.payments.aggregate(s=Sum("amount"))["s"] or Decimal("0")
            if paid and paid > 0:
                messages.error(
                    request,
                    "Cannot delete this fee line while payments exist. Adjust or remove payment records first.",
                )
                error_fee_id = fee.id
                error_fee_tab = "del"
            else:
                label = fee.fee_structure.fee_type.name
                fee.delete()
                messages.success(request, f"Removed fee line: {label}.")
                return redirect_back()

    fee_services.assign_missing_fees_for_student(student, ay)

    fee_qs = (
        Fee.objects.filter(student=student)
        .select_related("fee_structure__fee_type", "academic_year")
        .prefetch_related("payments")
        .order_by("due_date", "id")
    )
    if ay:
        fee_qs = fee_qs.filter(academic_year=ay)

    line_rows = []
    for fee in fee_qs:
        if error_fee_id == fee.id and error_adjust_form is not None:
            adj = error_adjust_form
        else:
            adj = FeeLineAdjustForm(instance=fee, prefix=f"adj{fee.id}")
        if fee.fee_structure.discount_allowed:
            if error_fee_id == fee.id and error_concession_form is not None:
                conc = error_concession_form
            else:
                conc = FeeConcessionForm(instance=fee, prefix=f"conc{fee.id}")
        else:
            conc = None
        line_rows.append(
            {
                "fee": fee,
                "adjust_form": adj,
                "concession_form": conc,
                "paid": fee_services.fee_amount_paid(fee),
                "balance": fee_services.fee_balance(fee),
                "sflx_initial_tab": (
                    (error_fee_tab or "amt")
                    if error_fee_id == fee.id
                    else "amt"
                ),
            }
        )

    return render(
        request,
        "core/billing/student_fee_lines.html",
        {
            "classroom": classroom,
            "student": student,
            "academic_year": ay,
            "academic_years": AcademicYear.objects.order_by("-start_date"),
            "line_rows": line_rows,
            "error_fee_id": error_fee_id,
        },
    )


@admin_required
@feature_required("fees")
def billing_concessions(request):
    """Concessions: one summary row per student (totals); expand to edit each fee line."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    from .forms import FeeConcessionForm

    ay = get_active_academic_year_obj()
    ay_param = (request.GET.get("ay") or "").strip()
    if ay_param:
        try:
            ay = AcademicYear.objects.get(pk=int(ay_param))
        except (ValueError, AcademicYear.DoesNotExist):
            pass

    # Accept both `class_id` (new UI) and `classroom_id` (legacy links/forms).
    classroom_id = None
    raw_class_id = (request.GET.get("class_id") or "").strip()
    raw_classroom_id = (request.GET.get("classroom_id") or "").strip()
    if raw_class_id.isdigit():
        classroom_id = int(raw_class_id)
    elif raw_classroom_id.isdigit():
        classroom_id = int(raw_classroom_id)
    qtext = (request.GET.get("q") or "").strip()
    page_num = int(request.GET.get("page") or 1)

    error_fee_id = None
    error_form = None

    if request.method == "POST" and request.POST.get("save_concession"):
        page_num = int(request.POST.get("page") or 1)
        if (p := (request.POST.get("ay") or "").strip()).isdigit():
            try:
                ay = AcademicYear.objects.get(pk=int(p))
            except AcademicYear.DoesNotExist:
                pass
        cpost = (request.POST.get("classroom_id") or "").strip()
        classroom_id = int(cpost) if cpost.isdigit() else None
        qtext = (request.POST.get("q") or "").strip()

        fee = get_object_or_404(
            Fee.objects.select_related("fee_structure", "student__user"),
            pk=int(request.POST.get("fee_id") or 0),
        )
        if not fee.fee_structure.discount_allowed:
            messages.error(
                request,
                "Concessions are turned off for this fee head. Open Class fee structure and enable “Discount allowed” on that line.",
            )
        else:
            form = FeeConcessionForm(request.POST, instance=fee)
            if form.is_valid():
                form.save()
                fee_services.refresh_fee_status_from_payments(fee)
                who = fee.student.user.get_full_name() or fee.student.user.username
                messages.success(request, f"Concession saved for {who}.")
                redir = {
                    k: v
                    for k, v in {
                        "ay": ay.id if ay else None,
                        "class_id": classroom_id,
                        "q": qtext,
                        "page": page_num,
                    }.items()
                    if v
                }
                target = reverse("core:billing_concessions")
                if redir:
                    target = f"{target}?{urlencode({k: str(v) for k, v in redir.items()})}"
                return redirect(target)
            error_fee_id = fee.id
            error_form = form
            messages.error(request, "Check the concession values and try again.")

    fee_qs = (
        Fee.objects.select_related("student__user", "student__classroom", "fee_structure__fee_type")
        .prefetch_related("payments")
        .order_by("student__user__last_name", "student__user__first_name", "due_date", "id")
    )
    if ay:
        fee_qs = fee_qs.filter(academic_year=ay)
    if classroom_id:
        fee_qs = fee_qs.filter(student__classroom_id=classroom_id)
    if qtext:
        fee_qs = fee_qs.filter(
            Q(student__user__first_name__icontains=qtext)
            | Q(student__user__last_name__icontains=qtext)
            | Q(student__user__username__icontains=qtext)
            | Q(student__admission_number__icontains=qtext)
            | Q(fee_structure__fee_type__name__icontains=qtext)
        )

    selected_classroom = None
    if classroom_id:
        selected_classroom = ClassRoom.objects.filter(pk=classroom_id).first()

    # Drill-down UX:
    # - First screen shows class cards (no `class_id` selected).
    # - Clicking a card sets `class_id`, which renders the existing student list + modals.
    show_class_cards = request.method == "GET" and not classroom_id and not qtext
    if show_class_cards:
        fees_for_cards = (
            Fee.objects.select_related("student__classroom")
            .filter(student__classroom__isnull=False)
            .order_by("student__classroom__grade_order", "student__classroom__name", "id")
        )
        if ay:
            fees_for_cards = fees_for_cards.filter(academic_year=ay)

        by_class: dict[int, dict] = {}
        for fee in fees_for_cards:
            cid = fee.student.classroom_id
            if not cid:
                continue
            if cid not in by_class:
                by_class[cid] = {
                    "classroom": fee.student.classroom,
                    "student_ids": set(),
                    "total_net_due": Decimal("0"),
                    "total_concession": Decimal("0"),
                }
            by_class[cid]["student_ids"].add(fee.student_id)
            by_class[cid]["total_net_due"] += fee.effective_due_amount
            by_class[cid]["total_concession"] += fee.total_concession_amount

        class_cards = []
        for cid, payload in by_class.items():
            class_cards.append(
                {
                    "classroom": payload["classroom"],
                    "students_count": len(payload["student_ids"]),
                    "total_net_due": payload["total_net_due"],
                    "total_concession": payload["total_concession"],
                }
            )

        return render(
            request,
            "core/billing/concessions.html",
            {
                "academic_year": ay,
                "academic_years": AcademicYear.objects.order_by("-start_date"),
                "classroom_id": None,
                "selected_classroom": None,
                "class_cards": class_cards,
                "q": "",
                # Keep shape stable for template blocks.
                "page_obj": None,
                "student_concession_groups": [],
                "error_fee_id": None,
                "open_student_id": None,
            },
        )

    students_qs = (
        Student.objects.filter(pk__in=fee_qs.values("student_id"))
        .select_related("user", "classroom")
        .order_by("user__last_name", "user__first_name", "pk")
    )

    paginator = Paginator(students_qs, 20)
    page_obj = paginator.get_page(page_num)

    page_student_ids = [s.pk for s in page_obj.object_list]
    fees_for_page = list(
        fee_qs.filter(student_id__in=page_student_ids)
        .select_related("student__user", "student__classroom", "fee_structure__fee_type")
        .prefetch_related("payments")
        .order_by("student_id", "due_date", "id")
    )
    by_student: dict[int, list] = defaultdict(list)
    for fee in fees_for_page:
        by_student[fee.student_id].append(fee)

    open_student_id = None
    if error_fee_id:
        open_student_id = Fee.objects.filter(pk=error_fee_id).values_list("student_id", flat=True).first()

    student_concession_groups = []
    for student in page_obj.object_list:
        fees = by_student.get(student.pk, [])
        rows = []
        tot_orig = Decimal("0")
        tot_conc = Decimal("0")
        tot_net = Decimal("0")
        for fee in fees:
            tot_orig += fee.amount or Decimal("0")
            tot_conc += fee.total_concession_amount
            tot_net += fee.effective_due_amount
            if error_fee_id is not None and fee.id == error_fee_id:
                rows.append({"fee": fee, "form": error_form})
            else:
                rows.append({"fee": fee, "form": FeeConcessionForm(instance=fee)})
        student_concession_groups.append(
            {
                "student": student,
                "rows": rows,
                "lines_count": len(fees),
                "total_original": tot_orig,
                "total_concession": tot_conc,
                "total_net": tot_net,
            }
        )

    return render(
        request,
        "core/billing/concessions.html",
        {
            "academic_year": ay,
            "academic_years": AcademicYear.objects.order_by("-start_date"),
            "classroom_id": classroom_id,
            "selected_classroom": selected_classroom,
            "classrooms": ClassRoom.objects.all().order_by(*ORDER_AY_START_GRADE_NAME),
            "q": qtext,
            "page_obj": page_obj,
            "student_concession_groups": student_concession_groups,
            "error_fee_id": error_fee_id,
            "open_student_id": open_student_id,
            "class_cards": [],
        },
    )


def _billing_resolve_ay(request):
    ay = get_active_academic_year_obj()
    ay_param = (request.GET.get("ay") or request.POST.get("ay") or "").strip()
    if ay_param.isdigit():
        try:
            ay = AcademicYear.objects.get(pk=int(ay_param))
        except AcademicYear.DoesNotExist:
            pass
    return ay


def _classroom_sections_payload():
    classrooms = ClassRoom.objects.prefetch_related("sections").order_by(*ORDER_AY_START_GRADE_NAME)
    sections_by_class = {
        c.id: [{"id": s.id, "name": s.name} for s in c.sections.all()] for c in classrooms
    }
    return classrooms, sections_by_class


def _redirect_after_payment_record_payment(request, student, ay):
    q = {}
    if ay:
        q["ay"] = ay.id
    if student.classroom_id:
        q["classroom_id"] = student.classroom_id
    if student.section_id:
        q["section_id"] = student.section_id
    q["student_id"] = student.id
    target = reverse("core:billing_record_payment")
    if q:
        target = f"{target}?{urlencode({k: str(v) for k, v in q.items()})}"
    return redirect(target)


def _redirect_after_payment_student_collect(student, ay):
    target = reverse("core:billing_student_collect", args=[student.pk])
    q = [("recorded", "1")]
    if ay:
        q.insert(0, ("ay", str(ay.id)))
    target = f"{target}?{urlencode(q)}"
    return redirect(target)


def _pay_amt_prefill_from_post(request) -> dict[str, str]:
    return {k[8:]: v for k, v in request.POST.items() if k.startswith("pay_amt_")}


def _payment_table_rows(bundle, pay_amt_prefill: dict[str, str]) -> list[dict]:
    if not bundle:
        return []
    return [
        {**t, "prefill_amt": pay_amt_prefill.get(str(t["id"]), "")}
        for t in bundle.get("payment_targets") or []
    ]


def _default_tender_rows() -> list[dict]:
    return [{"method": "Cash", "amount": "", "ref": ""}]


def _tender_rows_from_post(request) -> list[dict]:
    methods = request.POST.getlist("tender_method")
    amounts = request.POST.getlist("tender_amount")
    refs = request.POST.getlist("tender_ref")
    n = max(len(methods), len(amounts), len(refs), 1)
    rows = []
    for i in range(n):
        m = (methods[i].strip() if i < len(methods) and methods[i] else "").strip()
        rows.append(
            {
                "method": m or "Cash",
                "amount": amounts[i] if i < len(amounts) else "",
                "ref": refs[i] if i < len(refs) else "",
            }
        )
    return rows


def _parse_tenders_from_post(request) -> list[tuple[str, Decimal, str]] | None:
    from .forms import PaymentForm

    allowed = {c[0] for c in PaymentForm.PAYMENT_MODE_CHOICES}
    methods = request.POST.getlist("tender_method")
    amounts = request.POST.getlist("tender_amount")
    refs = request.POST.getlist("tender_ref")
    n = max(len(methods), len(amounts), len(refs))
    if n == 0:
        messages.error(request, "Add at least one payment method and amount.")
        return None
    out: list[tuple[str, Decimal, str]] = []
    for i in range(n):
        m = (methods[i].strip() if i < len(methods) and methods[i] else "").strip()
        a = (amounts[i].strip() if i < len(amounts) and amounts[i] else "").strip()
        r = (refs[i].strip() if i < len(refs) and refs[i] else "").strip()
        if not m and not a and not r:
            continue
        if not m:
            messages.error(request, "Select a payment mode for each amount entered.")
            return None
        if not a:
            messages.error(request, "Enter an amount for each payment mode.")
            return None
        try:
            amt = Decimal(a)
        except Exception:
            messages.error(request, "Enter valid amounts for payment methods.")
            return None
        if amt <= 0:
            messages.error(request, "Each payment method amount must be greater than zero.")
            return None
        if m not in allowed:
            messages.error(request, "Invalid payment mode selected.")
            return None
        out.append((m, amt.quantize(Decimal("0.01")), r))
    if not out:
        messages.error(request, "Add at least one payment method and amount.")
        return None
    return out


def _parse_fee_allocations_from_post(request, bundle) -> list[tuple[int, Decimal]] | None:
    """Return (fee_id, amount) rows or None if validation failed (messages set)."""
    targets = bundle.get("payment_targets") or []
    valid_ids = {t["id"] for t in targets}
    fee_ids = request.POST.getlist("fee_pay")
    seen: set[int] = set()
    out: list[tuple[int, Decimal]] = []
    for fid_s in fee_ids:
        if not fid_s.isdigit():
            continue
        fid = int(fid_s)
        if fid in seen:
            messages.error(request, "Each fee line can only be selected once.")
            return None
        seen.add(fid)
        if fid not in valid_ids:
            messages.error(request, "Invalid fee line selected.")
            return None
        raw = (request.POST.get(f"pay_amt_{fid}") or "").strip()
        if not raw:
            messages.error(request, "Enter an amount for each selected fee line.")
            return None
        try:
            amt = Decimal(raw)
        except Exception:
            messages.error(request, "Enter valid amounts.")
            return None
        if amt <= 0:
            messages.error(request, "Each amount must be greater than zero.")
            return None
        out.append((fid, amt.quantize(Decimal("0.01"))))
    if not out:
        messages.error(
            request,
            "Select at least one fee line and enter an amount greater than zero.",
        )
        return None
    return out


def _run_multi_fee_payment(
    request,
    student,
    ay,
    bundle,
    *,
    redirect_response,
):
    """
    If POST is a valid multi-line payment, save and return redirect.
    Otherwise return None (caller re-renders with posted form + table state).
    """
    from .forms import PaymentHeaderForm

    if request.method != "POST" or request.POST.get("form_action") != "record_payment":
        return None

    form = PaymentHeaderForm(request.POST)
    pairs = _parse_fee_allocations_from_post(request, bundle)
    if pairs is None:
        return None
    if not form.is_valid():
        return None

    fees = list(
        Fee.objects.filter(id__in=[p[0] for p in pairs], student_id=student.pk).select_related(
            "fee_structure__fee_type"
        )
    )
    by_id = {f.id: f for f in fees}
    allocations: list[tuple[Fee, Decimal]] = []
    for fid, amt in pairs:
        fee = by_id.get(fid)
        if not fee:
            messages.error(request, "Could not load a selected fee line.")
            return None
        allocations.append((fee, amt))

    tenders = _parse_tenders_from_post(request)
    if tenders is None:
        return None
    alloc_total = sum((a for _, a in allocations), Decimal("0"))
    tender_total = sum((t[1] for t in tenders), Decimal("0"))
    if alloc_total != tender_total:
        messages.error(
            request,
            "The sum of payment methods must equal the total allocated to fee lines for this receipt.",
        )
        return None

    try:
        batch = fee_services.record_fee_payment_batch(
            student=student,
            academic_year=ay,
            allocations=allocations,
            payment_date=form.cleaned_data["payment_date"],
            tenders=tenders,
            receipt_number=form.cleaned_data.get("receipt_number") or "",
            notes=form.cleaned_data.get("notes") or "",
            user=request.user,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return None

    n = len(allocations)
    messages.success(
        request,
        f"Recorded ₹{batch.total_amount} across {n} fee line{'s' if n != 1 else ''}.",
    )
    return redirect_response()


@admin_required
@feature_required("fees")
def billing_fee_student_search(request):
    """JSON: students in a class (optional section). Optional q narrows by name / admission."""
    school = _school_fee_check(request)
    if not school:
        return JsonResponse({"results": []}, status=403)
    cid = (request.GET.get("classroom_id") or "").strip()
    if not cid.isdigit():
        return JsonResponse({"results": []})
    qs = Student.objects.filter(classroom_id=int(cid), user__is_active=True).select_related(
        "user", "section"
    )
    sid = (request.GET.get("section_id") or "").strip()
    if sid.isdigit():
        qs = qs.filter(section_id=int(sid))
    qtext = (request.GET.get("q") or "").strip()
    if qtext:
        qs = qs.filter(
            Q(user__first_name__icontains=qtext)
            | Q(user__last_name__icontains=qtext)
            | Q(user__username__icontains=qtext)
            | Q(admission_number__icontains=qtext)
            | Q(roll_number__icontains=qtext)
        )
    limit = 80 if qtext else 200
    qs = qs.order_by("user__last_name", "user__first_name", "admission_number")[:limit]
    results = []
    for s in qs:
        name = s.user.get_full_name() or s.user.username
        adm = (s.admission_number or "").strip() or "—"
        sec = s.section.name if s.section_id else "—"
        results.append({"id": s.id, "text": name, "sub": f"Adm {adm} · Sec {sec}"})
    return JsonResponse({"results": results})


@admin_required
@feature_required("fees")
def billing_record_payment(request):
    """Find student by class / section / search, then record fee payments (multi-line)."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    from .forms import PaymentForm, PaymentHeaderForm

    ay = _billing_resolve_ay(request)
    classrooms, sections_by_class = _classroom_sections_payload()

    classroom_id = None
    raw_c = (request.GET.get("classroom_id") or request.POST.get("classroom_id") or "").strip()
    if raw_c.isdigit():
        classroom_id = int(raw_c)
    section_id = None
    raw_s = (request.GET.get("section_id") or request.POST.get("section_id") or "").strip()
    if raw_s.isdigit():
        section_id = int(raw_s)

    student = None
    raw_st = (request.GET.get("student_id") or request.POST.get("student_id") or "").strip()
    if raw_st.isdigit():
        student = get_object_or_404(
            Student.objects.select_related("user", "classroom", "section"), pk=int(raw_st)
        )

    payment_header_form = PaymentHeaderForm(initial={"payment_date": date.today()})
    pay_amt_prefill: dict[str, str] = {}
    fee_pay_selected: list[str] = []
    tender_rows = _default_tender_rows()
    bundle = None

    if student:
        bundle = fee_services.build_fee_collect_bundle(student, ay)
        if request.method == "POST" and request.POST.get("form_action") == "record_payment":
            pay_amt_prefill = _pay_amt_prefill_from_post(request)
            fee_pay_selected = request.POST.getlist("fee_pay")
            payment_header_form = PaymentHeaderForm(request.POST)
            tender_rows = _tender_rows_from_post(request)
            redir = _run_multi_fee_payment(
                request,
                student,
                ay,
                bundle,
                redirect_response=lambda: _redirect_after_payment_record_payment(request, student, ay),
            )
            if redir:
                return redir

    return render(
        request,
        "core/billing/fee_collect.html",
        {
            "show_student_finder": True,
            "academic_year": ay,
            "academic_years": AcademicYear.objects.order_by("-start_date"),
            "classrooms": classrooms,
            "sections_by_class": sections_by_class,
            "classroom_id": classroom_id,
            "section_id": section_id,
            "student": student,
            "bundle": bundle,
            "payment_header_form": payment_header_form,
            "pay_amt_prefill": pay_amt_prefill,
            "fee_pay_selected": fee_pay_selected,
            "payment_table_rows": _payment_table_rows(bundle, pay_amt_prefill),
            "tender_rows": tender_rows,
            "payment_mode_choices": PaymentForm.PAYMENT_MODE_CHOICES,
            "student_search_url": reverse("core:billing_fee_student_search"),
        },
    )


@admin_required
@feature_required("fees")
def billing_student_collect(request, student_id: int):
    """Collect fees for one student (e.g. from class roster): ledger, multi-line payment."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    from .forms import PaymentForm, PaymentHeaderForm

    ay = _billing_resolve_ay(request)
    student = get_object_or_404(
        Student.objects.select_related("user", "classroom", "section"), pk=student_id
    )

    bundle = fee_services.build_fee_collect_bundle(student, ay)
    payment_header_form = PaymentHeaderForm(initial={"payment_date": date.today()})
    pay_amt_prefill: dict[str, str] = {}
    fee_pay_selected: list[str] = []
    tender_rows = _default_tender_rows()

    if request.method == "POST" and request.POST.get("form_action") == "record_payment":
        pay_amt_prefill = _pay_amt_prefill_from_post(request)
        fee_pay_selected = request.POST.getlist("fee_pay")
        payment_header_form = PaymentHeaderForm(request.POST)
        tender_rows = _tender_rows_from_post(request)
        redir = _run_multi_fee_payment(
            request,
            student,
            ay,
            bundle,
            redirect_response=lambda: _redirect_after_payment_student_collect(student, ay),
        )
        if redir:
            return redir

    return render(
        request,
        "core/billing/fee_collect.html",
        {
            "show_student_finder": False,
            "academic_year": ay,
            "academic_years": AcademicYear.objects.order_by("-start_date"),
            "classrooms": (),
            "sections_by_class": {},
            "classroom_id": student.classroom_id,
            "section_id": student.section_id,
            "student": student,
            "bundle": bundle,
            "payment_header_form": payment_header_form,
            "pay_amt_prefill": pay_amt_prefill,
            "fee_pay_selected": fee_pay_selected,
            "payment_table_rows": _payment_table_rows(bundle, pay_amt_prefill),
            "tender_rows": tender_rows,
            "payment_mode_choices": PaymentForm.PAYMENT_MODE_CHOICES,
            "student_search_url": reverse("core:billing_fee_student_search"),
        },
    )


@admin_required
@feature_required("fees")
def billing_receipt_batch(request, batch_id: int):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    batch = get_object_or_404(
        PaymentBatch.objects.select_related(
            "student__user",
            "student__classroom",
            "student__section",
            "academic_year",
            "received_by",
        ).prefetch_related(
            "tenders",
            "line_payments__fee__fee_structure__fee_type",
        ),
        pk=batch_id,
    )
    from .billing_receipts import receipt_batch_context

    ctx = receipt_batch_context(batch, request=request)
    ctx["print_on_load"] = (request.GET.get("print") or "").strip() == "1"
    return render(request, "core/billing/receipt_fee.html", ctx)


@admin_required
@feature_required("fees")
def billing_receipt_batch_pdf(request, batch_id: int):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    batch = get_object_or_404(
        PaymentBatch.objects.select_related(
            "student__user",
            "student__classroom",
            "student__section",
            "academic_year",
            "received_by",
        ).prefetch_related(
            "tenders",
            "line_payments__fee__fee_structure__fee_type",
        ),
        pk=batch_id,
    )
    from .billing_receipts import receipt_batch_context
    from .pdf_utils import pdf_response, render_pdf_bytes

    ctx = receipt_batch_context(batch, request=request)
    ctx["is_pdf"] = True
    ctx["print_on_load"] = False
    receipt_no = (ctx.get("receipt_number") or f"batch-{batch.pk}").strip()
    pdf_bytes = render_pdf_bytes("core/billing/receipt_fee.html", ctx)
    if not pdf_bytes:
        return HttpResponse(
            "Receipt PDF could not be generated.",
            status=500,
            content_type="text/plain; charset=utf-8",
        )
    fn = re.sub(r"[^\w\-.]+", "_", receipt_no)[:120] + ".pdf"
    return pdf_response(pdf_bytes, fn)


@admin_required
@feature_required("fees")
def billing_receipt_payment(request, payment_id: int):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    payment = get_object_or_404(
        Payment.objects.select_related(
            "fee__student__user",
            "fee__student__classroom",
            "fee__student__section",
            "fee__fee_structure__fee_type",
            "fee__academic_year",
            "batch",
            "received_by",
        ),
        pk=payment_id,
    )
    if payment.batch_id:
        return redirect("core:billing_receipt_batch", batch_id=payment.batch_id)
    from .billing_receipts import receipt_orphan_payment_context

    ctx = receipt_orphan_payment_context(payment, request=request)
    ctx["print_on_load"] = (request.GET.get("print") or "").strip() == "1"
    return render(request, "core/billing/receipt_fee.html", ctx)


@admin_required
@feature_required("fees")
def billing_receipt_payment_pdf(request, payment_id: int):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    payment = get_object_or_404(
        Payment.objects.select_related(
            "fee__student__user",
            "fee__student__classroom",
            "fee__student__section",
            "fee__fee_structure__fee_type",
            "fee__academic_year",
            "batch",
            "received_by",
        ),
        pk=payment_id,
    )
    if payment.batch_id:
        return redirect("core:billing_receipt_batch_pdf", batch_id=payment.batch_id)
    from .billing_receipts import receipt_orphan_payment_context
    from .pdf_utils import pdf_response, render_pdf_bytes

    ctx = receipt_orphan_payment_context(payment, request=request)
    ctx["is_pdf"] = True
    ctx["print_on_load"] = False
    receipt_no = (ctx.get("receipt_number") or f"payment-{payment.pk}").strip()
    pdf_bytes = render_pdf_bytes("core/billing/receipt_fee.html", ctx)
    if not pdf_bytes:
        return HttpResponse(
            "Receipt PDF could not be generated.",
            status=500,
            content_type="text/plain; charset=utf-8",
        )
    fn = re.sub(r"[^\w\-.]+", "_", receipt_no)[:120] + ".pdf"
    return pdf_response(pdf_bytes, fn)


@admin_required
@feature_required("fees")
def billing_invoice_student(request, student_id: int):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    ay = _billing_resolve_ay(request)
    student = get_object_or_404(
        Student.objects.select_related("user", "classroom", "section"), pk=student_id
    )
    from .billing_receipts import student_invoice_context

    ctx = student_invoice_context(student, ay, request=request)
    ctx["print_on_load"] = False
    return render(request, "core/billing/invoice_student.html", ctx)


@admin_required
@feature_required("fees")
def billing_invoice_student_pdf(request, student_id: int):
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")
    ay = _billing_resolve_ay(request)
    student = get_object_or_404(
        Student.objects.select_related("user", "classroom", "section"), pk=student_id
    )
    from .billing_receipts import student_invoice_context
    from .pdf_utils import pdf_response, render_pdf_bytes

    ctx = student_invoice_context(student, ay, request=request)
    ctx["is_pdf"] = True
    ctx["print_on_load"] = False
    pdf_bytes = render_pdf_bytes("core/billing/invoice_student.html", ctx)
    if not pdf_bytes:
        return HttpResponse(
            "Statement PDF could not be generated.",
            status=500,
            content_type="text/plain; charset=utf-8",
        )
    name = (student.user.get_full_name() or student.user.username or "student").replace(" ", "_")
    suf = f"_AY{ay.pk}" if ay else "_all"
    fn = f"fee_statement_{name}{suf}.pdf"
    return pdf_response(pdf_bytes, fn)


def _billing_safe_next_path(request, default: str) -> str:
    n = (request.GET.get("next") or request.POST.get("next") or "").strip()
    if not n:
        return default
    if n.startswith("/") and not n.startswith("//") and "\n" not in n and ".." not in n:
        return n
    return default


def _default_record_payment_url(*, student_id: int, academic_year_id: int | None, classroom_id: int | None) -> str:
    q: dict[str, str] = {"student_id": str(student_id)}
    if academic_year_id is not None:
        q["ay"] = str(academic_year_id)
    if classroom_id is not None:
        q["classroom_id"] = str(classroom_id)
    return f"{reverse('core:billing_record_payment')}?{urlencode(q)}"


@admin_required
@feature_required("fees")
def billing_payment_batch_edit(request, batch_id: int):
    """Edit amounts and voucher metadata for a multi-line payment batch."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    from .forms import FeePaymentMetadataForm

    batch = get_object_or_404(
        PaymentBatch.objects.select_related(
            "student__user",
            "student__classroom",
            "student__section",
            "academic_year",
        ),
        pk=batch_id,
    )
    student = batch.student
    default_next = _default_record_payment_url(
        student_id=student.pk,
        academic_year_id=batch.academic_year_id,
        classroom_id=student.classroom_id,
    )
    next_url = _billing_safe_next_path(request, default_next)

    lines = list(
        batch.line_payments.select_related(
            "fee__fee_structure__fee_type",
        ).order_by("fee__fee_structure__fee_type__name", "id")
    )
    fee_choice_rows = fee_services.fee_choice_rows_for_batch_edit(batch)
    line_rows = [
        {
            "payment_id": p.pk,
            "fee_id": p.fee_id,
            "label": (
                p.fee.fee_structure.fee_type.name
                if p.fee.fee_structure_id and p.fee.fee_structure.fee_type_id
                else "—"
            ),
            "amount": p.amount,
        }
        for p in lines
    ]

    if request.method == "POST":
        form = FeePaymentMetadataForm(request.POST)
        parse_ok = True
        line_targets: dict[int, tuple[int, Decimal]] = {}
        if form.is_valid():
            for row in line_rows:
                pid = row["payment_id"]
                raw_amt = (request.POST.get(f"line_{pid}") or "").strip()
                raw_fee = (request.POST.get(f"fee_{pid}") or "").strip()
                try:
                    fid = int(raw_fee)
                    amt = Decimal(raw_amt)
                except (ValueError, InvalidOperation):
                    parse_ok = False
                    break
                line_targets[pid] = (fid, amt)
            if not parse_ok:
                messages.error(request, "Enter a valid fee type and amount for every line.")
            elif line_targets:
                try:
                    cd = form.cleaned_data
                    fee_services.update_payment_batch_allocations(
                        batch,
                        line_targets=line_targets,
                        payment_date=cd["payment_date"],
                        receipt_number=(cd.get("receipt_number") or "").strip(),
                        transaction_reference=(cd.get("transaction_reference") or "").strip(),
                        notes=(cd.get("notes") or "").strip(),
                    )
                    messages.success(request, "Payment updated.")
                    return redirect(next_url)
                except ValueError as exc:
                    messages.error(request, str(exc))
    else:
        form = FeePaymentMetadataForm(
            initial={
                "payment_date": batch.payment_date,
                "receipt_number": batch.receipt_number,
                "transaction_reference": batch.transaction_reference,
                "notes": batch.notes,
            }
        )

    return render(
        request,
        "core/billing/payment_edit.html",
        {
            "form": form,
            "batch": batch,
            "payment": None,
            "line_rows": line_rows,
            "fee_choice_rows": fee_choice_rows,
            "student": student,
            "next_url": next_url,
            "page_heading": "Edit payment",
            "page_sub": "Reassign fee types and amounts (totals cannot exceed net due per fee). Update voucher details below.",
            "payment_method_display": (batch.payment_method or "").strip() or "—",
            "orphan_max_amount": None,
        },
    )


@admin_required
@feature_required("fees")
def billing_orphan_payment_edit(request, payment_id: int):
    """Edit amount and voucher metadata for a legacy single-line payment (no batch)."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    from .forms import OrphanPaymentEditForm

    payment = get_object_or_404(
        Payment.objects.select_related(
            "fee__student__user",
            "fee__student__classroom",
            "fee__student__section",
            "fee__academic_year",
            "fee__fee_structure__fee_type",
        ),
        pk=payment_id,
    )
    if payment.batch_id:
        return redirect("core:billing_payment_batch_edit", batch_id=payment.batch_id)

    student = payment.fee.student
    ay_id = payment.fee.academic_year_id
    default_next = _default_record_payment_url(
        student_id=student.pk,
        academic_year_id=ay_id,
        classroom_id=student.classroom_id,
    )
    next_url = _billing_safe_next_path(request, default_next)
    max_amt = fee_services.max_amount_for_payment_line(payment)

    if request.method == "POST":
        form = OrphanPaymentEditForm(
            request.POST,
            max_amount=max_amt,
        )
        if form.is_valid():
            cd = form.cleaned_data
            try:
                fee_services.update_orphan_payment_line(
                    payment,
                    amount=cd["amount"],
                    payment_date=cd["payment_date"],
                    receipt_number=(cd.get("receipt_number") or "").strip(),
                    transaction_reference=(cd.get("transaction_reference") or "").strip(),
                    notes=(cd.get("notes") or "").strip(),
                )
                messages.success(request, "Payment updated.")
                return redirect(next_url)
            except ValueError as exc:
                messages.error(request, str(exc))
    else:
        form = OrphanPaymentEditForm(
            max_amount=max_amt,
            initial={
                "amount": payment.amount,
                "payment_date": payment.payment_date,
                "receipt_number": payment.receipt_number,
                "transaction_reference": payment.transaction_reference,
                "notes": payment.notes,
            },
        )

    return render(
        request,
        "core/billing/payment_edit.html",
        {
            "form": form,
            "batch": None,
            "payment": payment,
            "line_rows": None,
            "student": student,
            "next_url": next_url,
            "page_heading": "Edit payment",
            "page_sub": "Single-line receipt — adjust amount within balance due.",
            "payment_method_display": (payment.payment_method or "").strip() or "—",
            "orphan_max_amount": max_amt,
        },
    )
