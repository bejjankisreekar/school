import base64
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Q
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse

from apps.school_data.models import Teacher
from apps.customers.models import School
from apps.accounts.decorators import admin_required
from .models import SalaryComponent, SalaryStructure, SalaryAdvance, Payslip


def _school_required(view):
    def wrapped(request, *args, **kwargs):
        if not getattr(request.user, "school", None):
            return redirect("core:admin_dashboard")
        return view(request, *args, **kwargs)
    return wrapped


@admin_required
def payroll_dashboard(request):
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")

    employees = Teacher.objects.count()
    structures = SalaryStructure.objects.select_related("teacher__user")
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
    })


@admin_required
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
def salary_component_delete(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    comp = get_object_or_404(SalaryComponent, pk=pk)
    if request.method == "POST":
        comp.delete()
    return redirect("payroll:salary_components_list")


@admin_required
def salary_structure_list(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    structures = SalaryStructure.objects.select_related("teacher__user").order_by("teacher__user__first_name")
    return render(request, "payroll/salary_structure_list.html", {"structures": structures})


@admin_required
def salary_structure_add(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    from .forms import SalaryStructureForm
    form = SalaryStructureForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("payroll:salary_structure_list")
    return render(request, "payroll/salary_structure_form.html", {"form": form, "title": "Add Salary Structure"})


@admin_required
def salary_structure_edit(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    structure = get_object_or_404(SalaryStructure, pk=pk)
    from .forms import SalaryStructureForm
    form = SalaryStructureForm(request.POST or None, instance=structure)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("payroll:salary_structure_list")
    return render(request, "payroll/salary_structure_form.html", {"form": form, "title": "Edit Structure", "structure": structure})


@admin_required
def salary_structure_delete(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    structure = get_object_or_404(SalaryStructure, pk=pk)
    if request.method == "POST":
        structure.delete()
    return redirect("payroll:salary_structure_list")


@admin_required
def salary_advances_list(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    advances = SalaryAdvance.objects.select_related("teacher__user").order_by("-advance_date")
    return render(request, "payroll/advances_list.html", {"advances": advances})


@admin_required
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
def salary_advance_delete(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    advance = get_object_or_404(SalaryAdvance, pk=pk)
    if request.method == "POST":
        advance.delete()
    return redirect("payroll:salary_advances_list")


@admin_required
def payroll_generate(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    school = request.user.school
    month = request.GET.get("month", str(date.today().month))
    year = request.GET.get("year", str(date.today().year))
    try:
        month = int(month)
        year = int(year)
    except (TypeError, ValueError):
        month = date.today().month
        year = date.today().year

    structures = SalaryStructure.objects.select_related("teacher__user")
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
        })

    if request.method == "POST":
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
        return redirect("payroll:payslips_list")

    return render(request, "payroll/generate.html", {
        "rows": rows,
        "month": month,
        "year": year,
        "school": school,
    })


def _earnings_breakdown(structure):
    d = {"Basic Salary": float(structure.basic_salary)}
    for c in SalaryComponent.objects.filter(component_type=SalaryComponent.ComponentType.ALLOWANCE, is_active=True):
        d[c.name] = float(c.calculate(structure.basic_salary))
    return d


def _deductions_breakdown(structure, advance_deduction):
    d = {}
    for c in SalaryComponent.objects.filter(component_type=SalaryComponent.ComponentType.DEDUCTION, is_active=True):
        d[c.name] = float(c.calculate(structure.basic_salary))
    if advance_deduction > 0:
        d["Loan Deduction"] = float(advance_deduction)
    return d


@admin_required
def payslips_list(request):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    month = request.GET.get("month")
    year = request.GET.get("year")
    qs = Payslip.objects.select_related("teacher__user").order_by("-year", "-month", "teacher__user__first_name")
    if month:
        qs = qs.filter(month=month)
    if year:
        qs = qs.filter(year=year)
    return render(request, "payroll/payslips_list.html", {"payslips": qs})


@admin_required
def payslip_view(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    payslip = get_object_or_404(Payslip, pk=pk)
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

    return render(request, "payroll/payslip_print.html", {
        "payslip": payslip,
        "school": school,
        "qr_data_uri": qr_data_uri,
    })


@admin_required
def payslip_pdf(request, pk):
    if not request.user.school:
        return redirect("core:admin_dashboard")
    payslip = get_object_or_404(Payslip, pk=pk)
    school = request.user.school
    html = render_to_string("payroll/payslip_print.html", {
        "payslip": payslip,
        "school": school,
        "qr_data_uri": None,
    })
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
