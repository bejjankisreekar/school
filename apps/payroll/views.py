import base64
import calendar
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch, Sum, Q
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse

from apps.school_data.models import Teacher
from apps.customers.models import School
from apps.accounts.decorators import admin_required, feature_required
from .models import SalaryComponent, SalaryStructure, SalaryStructureComponent, SalaryAdvance, Payslip
from .payslip_display import build_payslip_template_context, payslip_template_for_school


def _payroll_modal_lines(structure, components):
    """Rows for generate modal: component, included, custom rate flag, calc, value."""
    links = {L.component_id: L for L in structure.component_links.all()}
    use_all = structure.use_default_salary_components
    out = []
    for c in components:
        included = True if use_all else (c.pk in links)
        link = links.get(c.pk)
        custom = bool(link and not link.use_component_default)
        if custom:
            calc = link.override_calculation_type
            val = link.override_value if link.override_value is not None else Decimal("0")
        else:
            calc = c.calculation_type
            val = c.value
        out.append({
            "c": c,
            "included": included,
            "custom": custom,
            "calc": calc,
            "value": val,
        })
    return out


def _parse_component_post(request, component_id: int):
    """(error_message|None, use_component_default, override_calc|None, override_value|None)."""
    if request.POST.get(f"c{component_id}_custom") != "1":
        return None, True, None, None
    calc = (request.POST.get(f"c{component_id}_calc") or "").strip()
    val_raw = (request.POST.get(f"c{component_id}_val") or "").strip()
    if calc not in (SalaryComponent.CalculationType.PERCENTAGE, SalaryComponent.CalculationType.FIXED):
        return "Invalid calculation type for a custom rate.", False, None, None
    try:
        val = Decimal(val_raw) if val_raw else Decimal("0")
    except Exception:
        return "Invalid number for a custom rate.", False, None, None
    if val < 0:
        return "Custom rate cannot be negative.", False, None, None
    if calc == SalaryComponent.CalculationType.PERCENTAGE and val > 100:
        return "Percentage cannot exceed 100.", False, None, None
    return None, False, calc, val


def _save_structure_components_from_post(request, structure) -> tuple[str | None, str]:
    """
    Apply salary component POST fields to structure.
    Returns (error_message, success_message). On error, success_message is ignored.
    """
    full_ids = set(
        SalaryComponent.objects.filter(
            is_active=True,
            component_type__in=[
                SalaryComponent.ComponentType.ALLOWANCE,
                SalaryComponent.ComponentType.DEDUCTION,
            ],
        ).values_list("pk", flat=True)
    )
    posted_set: set[int] = set()
    for x in request.POST.getlist("component_id"):
        if str(x).isdigit():
            i = int(x)
            if i in full_ids:
                posted_set.add(i)
    if posted_set - full_ids:
        return "Invalid component selection.", ""
    who = structure.teacher.user.get_full_name() or structure.teacher.user.username
    all_mode = posted_set == full_ids

    if not all_mode:
        SalaryStructureComponent.objects.filter(salary_structure=structure).delete()
        structure.use_default_salary_components = False
        structure.save(update_fields=["use_default_salary_components"])
        if not posted_set:
            return None, f"No salary heads selected for {who} (basic pay only)."
        for cid in posted_set:
            err, use_def, ocalc, oval = _parse_component_post(request, cid)
            if err:
                return err, ""
            SalaryStructureComponent.objects.create(
                salary_structure=structure,
                component_id=cid,
                use_component_default=use_def,
                override_calculation_type="" if use_def else (ocalc or ""),
                override_value=None if use_def else oval,
            )
        return None, f"Saved custom heads and rates for {who}."

    structure.use_default_salary_components = True
    structure.save(update_fields=["use_default_salary_components"])
    for cid in full_ids:
        err, use_def, ocalc, oval = _parse_component_post(request, cid)
        if err:
            return err, ""
        if use_def:
            SalaryStructureComponent.objects.filter(salary_structure=structure, component_id=cid).delete()
        else:
            SalaryStructureComponent.objects.update_or_create(
                salary_structure=structure,
                component_id=cid,
                defaults={
                    "use_component_default": False,
                    "override_calculation_type": ocalc or "",
                    "override_value": oval,
                },
            )
    SalaryStructureComponent.objects.filter(salary_structure=structure).exclude(component_id__in=full_ids).delete()
    if full_ids:
        return None, f"Saved salary heads for {who} (all active apply; custom rates where set)."
    return None, f"No active salary components for {who}."


def _redirect_payroll_next(request):
    n = (request.POST.get("next") or "").strip()
    if n.startswith("/") and not n.startswith("//"):
        return redirect(n)
    return redirect("payroll:payroll_generate")


def _school_required(view):
    def wrapped(request, *args, **kwargs):
        if not getattr(request.user, "school", None):
            return redirect("core:admin_dashboard")
        return view(request, *args, **kwargs)
    return wrapped


@admin_required
@feature_required("payroll")
def payroll_dashboard(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")

    if request.method == "POST" and request.POST.get("save_payslip_format"):
        fmt = (request.POST.get("payslip_format") or "").strip()
        valid = {c for c, _ in School.PayslipFormat.choices}
        if fmt in valid:
            school.payslip_format = fmt
            school.save(update_fields=["payslip_format"])
            messages.success(request, "Payslip layout saved for your school.")
        else:
            messages.error(request, "Invalid payslip layout.")
        return redirect("payroll:payroll_dashboard")

    employees = Teacher.objects.count()
    structures = SalaryStructure.objects.select_related("teacher__user").prefetch_related("applicable_components")
    total_monthly = sum(s.basic_salary + s.total_allowances() for s in structures)

    this_month = date.today().month
    this_year = date.today().year
    processed = Payslip.objects.filter(month=this_month, year=this_year).count()
    pending = employees - processed if employees else 0

    total_advances = SalaryAdvance.objects.filter(status=SalaryAdvance.Status.ACTIVE).aggregate(
        total=Sum("remaining_balance")
    )["total"] or Decimal("0")

    return render(request, "payroll/dashboard.html", {
        "total_employees": employees,
        "total_monthly_payroll": total_monthly,
        "pending_payroll": pending,
        "processed_payroll": processed,
        "total_advances": total_advances,
        "school": school,
        "payslip_format_choices": School.PayslipFormat.choices,
    })


@admin_required
@feature_required("payroll")
def salary_components_list(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    qs = SalaryComponent.objects.all().order_by("component_type", "order", "name")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(name__icontains=q)
    typ = request.GET.get("type")
    if typ in ("ALLOWANCE", "DEDUCTION"):
        qs = qs.filter(component_type=typ)
    status = request.GET.get("status")
    if status == "1":
        qs = qs.filter(is_active=True)
    elif status == "0":
        qs = qs.filter(is_active=False)
    return render(request, "payroll/components_list.html", {"components": qs})


@admin_required
@feature_required("payroll")
def salary_component_add(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    from .forms import SalaryComponentForm
    form = SalaryComponentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("payroll:salary_components_list")
    return render(request, "payroll/component_form.html", {"form": form, "title": "Add Component"})


@admin_required
@feature_required("payroll")
def salary_component_edit(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    comp = get_object_or_404(SalaryComponent, pk=pk)
    from .forms import SalaryComponentForm
    form = SalaryComponentForm(request.POST or None, instance=comp)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("payroll:salary_components_list")
    return render(request, "payroll/component_form.html", {"form": form, "title": "Edit Component", "component": comp})


@admin_required
@feature_required("payroll")
def salary_component_delete(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    comp = get_object_or_404(SalaryComponent, pk=pk)
    if request.method == "POST":
        comp.delete()
    return redirect("payroll:salary_components_list")


@admin_required
@feature_required("payroll")
def salary_structure_list(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    structures = (
        SalaryStructure.objects.select_related("teacher__user")
        .prefetch_related(
            Prefetch(
                "component_links",
                queryset=SalaryStructureComponent.objects.only(
                    "id", "salary_structure_id", "use_component_default",
                ),
            )
        )
        .order_by("teacher__user__first_name")
    )
    return render(request, "payroll/salary_structure_list.html", {"structures": structures})


@admin_required
@feature_required("payroll")
def salary_structure_add(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    from .forms import SalaryStructureForm
    form = SalaryStructureForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        messages.success(request, "Salary structure created. Set allowances and deductions below.")
        return redirect("payroll:salary_structure_edit", pk=obj.pk)
    return render(request, "payroll/salary_structure_form.html", {"form": form, "title": "Add Salary Structure"})


@admin_required
@feature_required("payroll")
def salary_structure_edit(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    structure = get_object_or_404(
        SalaryStructure.objects.select_related("teacher__user").prefetch_related(
            Prefetch(
                "component_links",
                queryset=SalaryStructureComponent.objects.select_related("component"),
            )
        ),
        pk=pk,
    )
    from .forms import SalaryStructureForm
    form = SalaryStructureForm(request.POST or None, instance=structure)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            err, _comp_msg = _save_structure_components_from_post(request, structure)
            if err:
                transaction.set_rollback(True)
                messages.error(request, err)
            else:
                messages.success(request, "Salary structure saved.")
        if not err:
            return redirect("payroll:salary_structure_edit", pk=pk)
        structure = get_object_or_404(
            SalaryStructure.objects.select_related("teacher__user").prefetch_related(
                Prefetch(
                    "component_links",
                    queryset=SalaryStructureComponent.objects.select_related("component"),
                )
            ),
            pk=pk,
        )
        form = SalaryStructureForm(request.POST, instance=structure)

    all_allow = list(
        SalaryComponent.objects.filter(
            component_type=SalaryComponent.ComponentType.ALLOWANCE,
            is_active=True,
        ).order_by("order", "name")
    )
    all_ded = list(
        SalaryComponent.objects.filter(
            component_type=SalaryComponent.ComponentType.DEDUCTION,
            is_active=True,
        ).order_by("order", "name")
    )
    allowance_lines = _payroll_modal_lines(structure, all_allow)
    deduction_lines = _payroll_modal_lines(structure, all_ded)

    advance_ded = Decimal("0")
    for adv in SalaryAdvance.objects.filter(teacher=structure.teacher, status=SalaryAdvance.Status.ACTIVE):
        if adv.remaining_balance > 0:
            advance_ded += min(adv.monthly_deduction, adv.remaining_balance)
    structure_preview = {
        "allowances": structure.total_allowances(),
        "deductions": structure.total_deductions(advance_ded),
        "advance": advance_ded,
        "net": structure.net_salary(advance_ded),
    }

    return render(
        request,
        "payroll/salary_structure_form.html",
        {
            "form": form,
            "title": "Edit Structure",
            "structure": structure,
            "allowance_lines": allowance_lines,
            "deduction_lines": deduction_lines,
            "structure_preview": structure_preview,
        },
    )


@admin_required
@feature_required("payroll")
def salary_structure_delete(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    structure = get_object_or_404(SalaryStructure, pk=pk)
    if request.method == "POST":
        structure.delete()
    return redirect("payroll:salary_structure_list")


@admin_required
@feature_required("payroll")
def salary_advances_list(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    advances = SalaryAdvance.objects.select_related("teacher__user").order_by("-advance_date")
    return render(request, "payroll/advances_list.html", {"advances": advances})


@admin_required
@feature_required("payroll")
def salary_advance_add(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    from .forms import SalaryAdvanceForm
    form = SalaryAdvanceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.remaining_balance = obj.amount
        obj.save()
        return redirect("payroll:salary_advances_list")
    return render(request, "payroll/advance_form.html", {"form": form, "title": "Add Advance"})


@admin_required
@feature_required("payroll")
def salary_advance_edit(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    advance = get_object_or_404(SalaryAdvance, pk=pk)
    from .forms import SalaryAdvanceForm
    form = SalaryAdvanceForm(request.POST or None, instance=advance)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("payroll:salary_advances_list")
    return render(request, "payroll/advance_form.html", {"form": form, "title": "Edit Advance", "advance": advance})


@admin_required
@feature_required("payroll")
def salary_advance_delete(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    advance = get_object_or_404(SalaryAdvance, pk=pk)
    if request.method == "POST":
        advance.delete()
    return redirect("payroll:salary_advances_list")


@admin_required
@feature_required("payroll")
def salary_structure_components_save(request, pk):
    """POST: which heads apply + optional per-head % or fixed override (e.g. from payroll generate)."""
    if request.method != "POST":
        return redirect("payroll:payroll_generate")
    if not request.user.school:
        return redirect("core:admin_dashboard")
    structure = get_object_or_404(SalaryStructure, pk=pk)
    err, success_msg = _save_structure_components_from_post(request, structure)
    if err:
        messages.error(request, err)
    else:
        messages.success(request, success_msg)
    return _redirect_payroll_next(request)


@admin_required
@feature_required("payroll")
def payroll_generate(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    school = request.user.school

    if request.method == "POST":
        raw_m = request.POST.get("month")
        raw_y = request.POST.get("year")
    else:
        raw_m = request.GET.get("month", str(date.today().month))
        raw_y = request.GET.get("year", str(date.today().year))
    try:
        month = int(raw_m)
        year = int(raw_y)
    except (TypeError, ValueError):
        month = date.today().month
        year = date.today().year
    if not 1 <= month <= 12:
        month = date.today().month
    if year < 2000 or year > 2100:
        year = date.today().year

    all_allow = list(
        SalaryComponent.objects.filter(
            component_type=SalaryComponent.ComponentType.ALLOWANCE,
            is_active=True,
        ).order_by("order", "name")
    )
    all_ded = list(
        SalaryComponent.objects.filter(
            component_type=SalaryComponent.ComponentType.DEDUCTION,
            is_active=True,
        ).order_by("order", "name")
    )
    structures = SalaryStructure.objects.select_related("teacher__user").prefetch_related(
        Prefetch(
            "component_links",
            queryset=SalaryStructureComponent.objects.select_related("component"),
        )
    )
    rows = []
    for s in structures:
        advance_ded = Decimal("0")
        for adv in SalaryAdvance.objects.filter(teacher=s.teacher, status=SalaryAdvance.Status.ACTIVE):
            if adv.remaining_balance > 0:
                advance_ded += min(adv.monthly_deduction, adv.remaining_balance)

        allowances = s.total_allowances()
        deductions = s.total_deductions(advance_ded)
        net = s.net_salary(advance_ded)
        rows.append({
            "structure": s,
            "allowances": allowances,
            "deductions": deductions,
            "advance_deduction": advance_ded,
            "net_salary": net,
            "allowance_lines": _payroll_modal_lines(s, all_allow),
            "deduction_lines": _payroll_modal_lines(s, all_ded),
        })

    grand_basic = sum((r["structure"].basic_salary for r in rows), Decimal("0"))
    grand_allowances = sum((r["allowances"] for r in rows), Decimal("0"))
    grand_deductions = sum((r["deductions"] for r in rows), Decimal("0"))
    grand_advance = sum((r["advance_deduction"] for r in rows), Decimal("0"))
    grand_net = sum((r["net_salary"] for r in rows), Decimal("0"))
    existing_payslips = Payslip.objects.filter(month=month, year=year).count()

    payslip_payment_selected = Payslip.normalize_payment_method(
        request.POST.get("payment_method") if request.method == "POST" else None
    )

    if request.method == "POST":
        if not rows:
            messages.error(request, "No salary structures to process. Add salary structures first.")
            return redirect("payroll:payroll_generate")
        # Generate payslips
        for r in rows:
            Payslip.objects.update_or_create(
                teacher=r["structure"].teacher,
                month=month,
                year=year,
                defaults={
                    "basic_salary": r["structure"].basic_salary,
                    "total_allowances": r["allowances"],
                    "total_deductions": r["deductions"],
                    "advance_deduction": r["advance_deduction"],
                    "net_salary": r["net_salary"],
                    "earnings_breakdown": _earnings_breakdown(r["structure"]),
                    "deductions_breakdown": _deductions_breakdown(r["structure"], r["advance_deduction"]),
                    "payment_method": payslip_payment_selected,
                    "status": Payslip.Status.PROCESSED,
                },
            )
        # Update advance balances
        for adv in SalaryAdvance.objects.filter(status=SalaryAdvance.Status.ACTIVE):
            if adv.remaining_balance > 0:
                adv.remaining_balance -= min(adv.monthly_deduction, adv.remaining_balance)
                if adv.remaining_balance <= 0:
                    adv.status = SalaryAdvance.Status.COMPLETED
                adv.save()
        label = f"{calendar.month_name[month]} {year}"
        messages.success(
            request,
            f"Payroll run complete for {label}: {len(rows)} payslip(s) saved or updated. Advance balances adjusted.",
        )
        return redirect(f"{reverse('payroll:payslips_list')}?month={month}&year={year}")

    year_choices = range(date.today().year - 1, date.today().year + 2)
    month_choices = [(i, calendar.month_name[i]) for i in range(1, 13)]

    return render(request, "payroll/generate.html", {
        "rows": rows,
        "month": month,
        "year": year,
        "month_name": calendar.month_name[month],
        "school": school,
        "grand_basic": grand_basic,
        "grand_allowances": grand_allowances,
        "grand_deductions": grand_deductions,
        "grand_advance": grand_advance,
        "grand_net": grand_net,
        "existing_payslips": existing_payslips,
        "year_choices": year_choices,
        "month_choices": month_choices,
        "payslip_payment_choices": Payslip.PaymentMethod.choices,
        "payslip_payment_selected": payslip_payment_selected,
    })


def _earnings_breakdown(structure):
    d = {"Basic Salary": float(structure.basic_salary)}
    for c in structure.applicable_allowances():
        d[c.name] = float(structure.amount_for_component(c))
    return d


def _deductions_breakdown(structure, advance_deduction):
    d = {}
    for c in structure.applicable_deductions():
        d[c.name] = float(structure.amount_for_component(c))
    if advance_deduction > 0:
        d["Loan Deduction"] = float(advance_deduction)
    return d


@admin_required
@feature_required("payroll")
def payslips_list(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    month_raw = request.GET.get("month", "").strip()
    year_raw = request.GET.get("year", "").strip()
    qs = Payslip.objects.select_related("teacher__user").order_by("-year", "-month", "teacher__user__first_name")
    sel_month = None
    sel_year = None
    if month_raw.isdigit():
        m = int(month_raw)
        if 1 <= m <= 12:
            sel_month = m
            qs = qs.filter(month=m)
    if year_raw.isdigit():
        sel_year = int(year_raw)
        qs = qs.filter(year=sel_year)
    today = date.today()
    month_choices = [(i, calendar.month_name[i]) for i in range(1, 13)]
    year_choices = list(range(today.year - 3, today.year + 2))
    return render(request, "payroll/payslips_list.html", {
        "payslips": qs,
        "month_choices": month_choices,
        "year_choices": year_choices,
        "sel_month": sel_month,
        "sel_year": sel_year,
    })


@admin_required
@feature_required("payroll")
def payslip_view(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    payslip = get_object_or_404(Payslip.objects.select_related("teacher__user"), pk=pk)
    school = request.user.school

    qr_data_uri = None
    try:
        import qrcode
        url = request.build_absolute_uri(reverse("payroll:payslip_view", args=[payslip.pk]))
        qr = qrcode.QRCode(version=1, box_size=4, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        qr_data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        pass

    period_label = date(payslip.year, payslip.month, 1).strftime("%B %Y")
    ctx = build_payslip_template_context(request, payslip, school, period_label, qr_data_uri)
    template_name = payslip_template_for_school(school)
    return render(request, template_name, ctx)


@admin_required
@feature_required("payroll")
def payslip_pdf(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    payslip = get_object_or_404(Payslip.objects.select_related("teacher__user"), pk=pk)
    school = request.user.school
    period_label = date(payslip.year, payslip.month, 1).strftime("%B %Y")
    ctx = build_payslip_template_context(request, payslip, school, period_label, None)
    template_name = payslip_template_for_school(school)
    html = render_to_string(template_name, ctx)
    try:
        from xhtml2pdf import pisa
        result = BytesIO()
        pisa.CreatePDF(html, dest=result, encoding="utf-8")
        result.seek(0)
        filename = f"payslip-{payslip.teacher.user.get_full_name() or payslip.teacher.user.username}-{payslip.month}-{payslip.year}.pdf"
        response = HttpResponse(result.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except ImportError:
        return redirect("payroll:payslip_view", pk=pk)
