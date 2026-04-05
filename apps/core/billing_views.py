"""School Fees & Billing — dashboard and class fee structure."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal

from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.utils import get_active_academic_year_obj
from apps.school_data.models import AcademicYear, ClassRoom, Fee, FeeStructure, FeeType, Student

from . import fee_services
from .views import _school_fee_check, admin_required, feature_required


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
def billing_structure_impacted_count(request):
    """JSON: active student count for class / optional section (fee structure preview)."""
    school = _school_fee_check(request)
    if not school:
        return JsonResponse({"count": 0}, status=403)
    cid = (request.GET.get("classroom_id") or "").strip()
    sid = (request.GET.get("section_id") or "").strip()
    if not cid.isdigit():
        return JsonResponse({"count": 0})
    classroom_id = int(cid)
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
                ).order_by("classroom__name", "academic_year__name", "fee_type__name"),
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

    show_add_modal = bool(
        request.method == "POST" and request.POST.get("save_fee_type") and not form_t.is_valid()
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

    from .forms import FeeStructureForm

    form_s = FeeStructureForm(school)
    open_fee_line_modal = False
    if request.method == "POST":
        if request.POST.get("save_fee_structure"):
            form_s = FeeStructureForm(school, request.POST)
            if form_s.is_valid():
                obj = form_s.save(commit=False)
                obj.save_with_audit(request.user)
                n_auto, err_auto = fee_services.auto_assign_fees_for_structure(obj)
                if err_auto:
                    messages.warning(request, err_auto)
                elif n_auto:
                    messages.success(
                        request,
                        f"Fee line saved. Auto-assigned {n_auto} new student fee due(s) for this class/section.",
                    )
                else:
                    msg = "Fee structure line saved."
                    if not obj.classroom_id:
                        msg += " Select a class to auto-create dues for students."
                    else:
                        msg += " No new dues (same due date may already exist, or no matching active students)."
                    messages.success(request, msg)
                return redirect(reverse("core:billing_fee_structure") + (f"?ay={ay.id}" if ay else ""))
            messages.error(request, "Check structure fields and try again.")
            open_fee_line_modal = True

    class_cards = fee_services.build_class_fee_structure_cards(ay)

    return render(
        request,
        "core/billing/class_fee_structure.html",
        {
            "form_s": form_s,
            "impacted_count_url": reverse("core:billing_structure_impacted_count"),
            "all_classrooms": ClassRoom.objects.all().order_by("name"),
            "academic_year": ay,
            "academic_years": AcademicYear.objects.order_by("-start_date"),
            "class_cards": class_cards,
            "open_fee_line_modal": open_fee_line_modal,
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

    rows = fee_services.build_classroom_student_fee_rollups(classroom_id, ay)
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
            "rows": rows,
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

    classroom_id = None
    if (v := (request.GET.get("classroom_id") or "").strip()).isdigit():
        classroom_id = int(v)
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
                redir = {k: v for k, v in {"ay": ay.id if ay else None, "classroom_id": classroom_id, "q": qtext, "page": page_num}.items() if v}
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
            "classrooms": ClassRoom.objects.all().order_by("name"),
            "q": qtext,
            "page_obj": page_obj,
            "student_concession_groups": student_concession_groups,
            "error_fee_id": error_fee_id,
            "open_student_id": open_student_id,
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
    classrooms = ClassRoom.objects.prefetch_related("sections").order_by("name")
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
    if ay:
        target = f"{target}?ay={ay.id}"
    return redirect(target)


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
    """Find student by class / section / search, then record full or partial fee payments."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    from .forms import PaymentForm

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

    form = None
    selected_pay_fee_id = None
    bundle = None

    if student:
        bundle = fee_services.build_fee_collect_bundle(student, ay)
        if request.method == "POST" and request.POST.get("form_action") == "record_payment":
            fee = get_object_or_404(Fee, pk=int(request.POST.get("payment_fee_id") or 0))
            if fee.student_id != student.pk:
                messages.error(request, "That fee line does not belong to this student.")
            else:
                form = PaymentForm(request.POST, fee=fee)
                if form.is_valid():
                    pay = form.save(commit=False)
                    pay.fee = fee
                    pay.received_by = request.user
                    pay.save()
                    fee_services.refresh_fee_status_from_payments(fee)
                    messages.success(
                        request,
                        f"Recorded ₹{pay.amount} for {fee.fee_structure.fee_type.name}.",
                    )
                    return _redirect_after_payment_record_payment(request, student, ay)
                selected_pay_fee_id = fee.id
                messages.error(request, "Check the payment amount and fields.")
        if form is None:
            targets = bundle["payment_targets"]
            fee_for_form = None
            if targets:
                fee_for_form = get_object_or_404(Fee, pk=targets[0]["id"])
            initial = {"payment_date": date.today()}
            if targets:
                initial["amount"] = targets[0]["balance"]
            form = PaymentForm(fee=fee_for_form, initial=initial)

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
            "form": form,
            "selected_pay_fee_id": selected_pay_fee_id,
            "student_search_url": reverse("core:billing_fee_student_search"),
        },
    )


@admin_required
@feature_required("fees")
def billing_student_collect(request, student_id: int):
    """Collect fees for one student (e.g. from class roster): ledger, full/partial payment."""
    school = _school_fee_check(request)
    if not school:
        return redirect("core:admin_dashboard")

    from .forms import PaymentForm

    ay = _billing_resolve_ay(request)
    student = get_object_or_404(
        Student.objects.select_related("user", "classroom", "section"), pk=student_id
    )

    bundle = fee_services.build_fee_collect_bundle(student, ay)
    form = None
    selected_pay_fee_id = None

    if request.method == "POST" and request.POST.get("form_action") == "record_payment":
        fee = get_object_or_404(Fee, pk=int(request.POST.get("payment_fee_id") or 0))
        if fee.student_id != student.pk:
            messages.error(request, "That fee line does not belong to this student.")
        else:
            form = PaymentForm(request.POST, fee=fee)
            if form.is_valid():
                pay = form.save(commit=False)
                pay.fee = fee
                pay.received_by = request.user
                pay.save()
                fee_services.refresh_fee_status_from_payments(fee)
                messages.success(
                    request,
                    f"Recorded ₹{pay.amount} for {fee.fee_structure.fee_type.name}.",
                )
                return _redirect_after_payment_student_collect(student, ay)
            selected_pay_fee_id = fee.id
            messages.error(request, "Check the payment amount and fields.")

    if form is None:
        targets = bundle["payment_targets"]
        fee_for_form = None
        if targets:
            fee_for_form = get_object_or_404(Fee, pk=targets[0]["id"])
        initial = {"payment_date": date.today()}
        if targets:
            initial["amount"] = targets[0]["balance"]
        form = PaymentForm(fee=fee_for_form, initial=initial)

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
            "form": form,
            "selected_pay_fee_id": selected_pay_fee_id,
            "student_search_url": reverse("core:billing_fee_student_search"),
        },
    )
