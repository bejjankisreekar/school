"""
Super Admin Control Center — analytics JSON APIs and CSV export.
"""
from __future__ import annotations

import csv
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.db import DatabaseError, connection, transaction
from django.db.models import Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounts.decorators import superadmin_required
from apps.customers.models import School, SchoolGeneratedInvoice, SaaSPlatformPayment

from .models import PlanName


def _safe_tenant_counts(school: School) -> tuple[int, int]:
    try:
        from django_tenants.utils import tenant_context

        from apps.school_data.models import Student, Teacher

        with tenant_context(school):
            with transaction.atomic():
                return (Student.objects.count(), Teacher.objects.count())
    except DatabaseError:
        try:
            if not connection.in_atomic_block:
                connection.rollback()
        except Exception:
            pass
        return (0, 0)
    except Exception:
        return (0, 0)


def _effective_free_until_date(school: School) -> date | None:
    fu = getattr(school, "saas_free_until_date", None)
    if fu:
        return fu
    return getattr(school, "saas_billing_complimentary_until", None)


def _effective_billing_start_date(school: School) -> date | None:
    if getattr(school, "billing_start_date", None):
        return school.billing_start_date
    fu = _effective_free_until_date(school)
    if fu:
        return fu + timedelta(days=1)
    return None


def _school_in_free_period(school: School, today: date) -> bool:
    start = _effective_billing_start_date(school)
    if start is None:
        return False
    return today < start


def _parse_month_param(raw: str | None, default: date | None = None) -> tuple[int, int]:
    today = default or timezone.localdate()
    s = (raw or "").strip()
    if len(s) >= 7 and s[4:5] == "-":
        try:
            y = int(s[:4])
            m = int(s[5:7])
            if 1 <= m <= 12 and 2000 <= y <= 2100:
                return y, m
        except ValueError:
            pass
    return today.year, today.month


def _month_key(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def _month_bounds(y: int, m: int) -> tuple[date, date]:
    last = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last)


def _prev_month(y: int, m: int) -> tuple[int, int]:
    if m == 1:
        return y - 1, 12
    return y, m - 1


def _last_n_calendar_months(n: int) -> list[tuple[int, int]]:
    today = timezone.localdate()
    y, m = today.year, today.month
    seq: list[tuple[int, int]] = []
    for _ in range(n):
        seq.append((y, m))
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
    return list(reversed(seq))


def _analytics_school_ids(request) -> list[int] | None:
    connection.set_schema_to_public()
    has_filter = False
    qs = School.objects.exclude(schema_name="public")
    plan = (request.GET.get("plan") or "").strip().lower()
    if plan == "none":
        has_filter = True
        qs = qs.filter(plan__isnull=True)
    elif plan in (PlanName.BASIC, PlanName.PRO, PlanName.PREMIUM):
        has_filter = True
        qs = qs.filter(plan__name=plan)
    st = (request.GET.get("status") or "").strip().lower()
    if st in {c[0] for c in School.SchoolStatus.choices}:
        has_filter = True
        qs = qs.filter(school_status=st)
    if not has_filter:
        return None
    return list(qs.values_list("id", flat=True))


def _payment_qs(school_ids: list[int] | None):
    qs = SaaSPlatformPayment.objects.all()
    if school_ids is not None:
        qs = qs.filter(school_id__in=school_ids)
    return qs


def _invoice_qs(school_ids: list[int] | None):
    qs = SchoolGeneratedInvoice.objects.exclude(status=SchoolGeneratedInvoice.Status.VOID)
    if school_ids is not None:
        qs = qs.filter(school_id__in=school_ids)
    return qs


def _money(v) -> float:
    if v is None:
        return 0.0
    return float(Decimal(v).quantize(Decimal("0.01")))


def _empty_summary(month_key: str):
    return JsonResponse(
        {
            "ok": True,
            "month": month_key,
            "kpis": {
                "total_revenue_all_time": 0.0,
                "revenue_this_month": 0.0,
                "revenue_last_month": 0.0,
                "revenue_mom_pct": None,
                "pending_payments": 0.0,
                "active_schools": 0,
                "total_students": 0,
                "revenue_per_student": None,
            },
        }
    )


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def analytics_summary_api(request):
    connection.set_schema_to_public()
    school_ids = _analytics_school_ids(request)
    y, m = _parse_month_param(request.GET.get("month"))
    mk = _month_key(y, m)
    if school_ids is not None and len(school_ids) == 0:
        return _empty_summary(mk)

    y0, m0 = _prev_month(y, m)
    d_start, d_end = _month_bounds(y, m)
    d0_start, d0_end = _month_bounds(y0, m0)

    pay_qs = _payment_qs(school_ids)
    inv_qs = _invoice_qs(school_ids)

    total_revenue = pay_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    this_month = pay_qs.filter(payment_date__gte=d_start, payment_date__lte=d_end).aggregate(
        s=Sum("amount")
    )["s"] or Decimal("0")
    last_month = pay_qs.filter(payment_date__gte=d0_start, payment_date__lte=d0_end).aggregate(
        s=Sum("amount")
    )["s"] or Decimal("0")

    pending = inv_qs.filter(status=SchoolGeneratedInvoice.Status.ISSUED).aggregate(
        s=Sum("grand_total")
    )["s"] or Decimal("0")

    sch_qs = School.objects.exclude(schema_name="public")
    if school_ids is not None:
        sch_qs = sch_qs.filter(pk__in=school_ids)
    active_schools = sch_qs.filter(
        school_status__in=(School.SchoolStatus.ACTIVE, School.SchoolStatus.TRIAL)
    ).count()

    total_students = 0
    for s in sch_qs.only("id", "schema_name"):
        stu, _ = _safe_tenant_counts(s)
        total_students += int(stu)

    mom_pct = None
    if last_month > 0:
        mom_pct = float(((this_month - last_month) / last_month) * 100)
    elif this_month > 0:
        mom_pct = 100.0

    rev_per_student = None
    if total_students > 0:
        rev_per_student = _money(total_revenue / Decimal(total_students))

    return JsonResponse(
        {
            "ok": True,
            "month": mk,
            "kpis": {
                "total_revenue_all_time": _money(total_revenue),
                "revenue_this_month": _money(this_month),
                "revenue_last_month": _money(last_month),
                "revenue_mom_pct": round(mom_pct, 1) if mom_pct is not None else None,
                "pending_payments": _money(pending),
                "active_schools": active_schools,
                "total_students": total_students,
                "revenue_per_student": rev_per_student,
            },
        }
    )


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def analytics_revenue_trend_api(request):
    connection.set_schema_to_public()
    school_ids = _analytics_school_ids(request)
    if school_ids is not None and len(school_ids) == 0:
        return JsonResponse({"ok": True, "labels": [], "values": []})

    pay_qs = _payment_qs(school_ids)
    months = _last_n_calendar_months(12)
    first = date(months[0][0], months[0][1], 1)
    rows = (
        pay_qs.filter(payment_date__gte=first)
        .annotate(m=TruncMonth("payment_date"))
        .values("m")
        .annotate(total=Sum("amount"))
    )
    by_month: dict[str, Decimal] = {}
    for r in rows:
        if r["m"]:
            by_month[r["m"].strftime("%Y-%m")] = r["total"] or Decimal("0")

    labels = []
    values = []
    for yy, mm in months:
        labels.append(date(yy, mm, 1).strftime("%b %Y"))
        values.append(_money(by_month.get(_month_key(yy, mm), Decimal("0"))))

    return JsonResponse({"ok": True, "labels": labels, "values": values})


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def analytics_school_revenue_api(request):
    connection.set_schema_to_public()
    school_ids = _analytics_school_ids(request)
    sort = (request.GET.get("sort") or "high").strip().lower()
    y, m = _parse_month_param(request.GET.get("month"))
    d_start, d_end = _month_bounds(y, m)

    sch_base = School.objects.exclude(schema_name="public").select_related("plan")
    if school_ids is not None:
        if not school_ids:
            return JsonResponse({"ok": True, "schools": []})
        sch_base = sch_base.filter(pk__in=school_ids)

    id_list = list(sch_base.values_list("id", flat=True))
    if not id_list:
        return JsonResponse({"ok": True, "schools": []})

    pay_by_school = defaultdict(lambda: Decimal("0"))
    for row in SaaSPlatformPayment.objects.filter(school_id__in=id_list).values("school_id").annotate(
        t=Sum("amount")
    ):
        pay_by_school[row["school_id"]] = row["t"] or Decimal("0")

    pending_by_school = defaultdict(lambda: Decimal("0"))
    for row in (
        SchoolGeneratedInvoice.objects.filter(
            school_id__in=id_list,
            status=SchoolGeneratedInvoice.Status.ISSUED,
        )
        .values("school_id")
        .annotate(t=Sum("grand_total"))
    ):
        pending_by_school[row["school_id"]] = row["t"] or Decimal("0")

    month_pay_by_school = defaultdict(lambda: Decimal("0"))
    for row in SaaSPlatformPayment.objects.filter(
        school_id__in=id_list,
        payment_date__gte=d_start,
        payment_date__lte=d_end,
    ).values("school_id").annotate(t=Sum("amount")):
        month_pay_by_school[row["school_id"]] = row["t"] or Decimal("0")

    out = []
    for s in sch_base:
        sid = s.pk
        plan_label = "No plan"
        if s.plan_id:
            plan_label = (
                "Premium · Enterprise"
                if s.plan.name == PlanName.PREMIUM
                else s.plan.get_name_display()
            )
        out.append(
            {
                "id": sid,
                "name": s.name,
                "plan": plan_label,
                "status": s.school_status,
                "total_paid": _money(pay_by_school[sid]),
                "pending": _money(pending_by_school[sid]),
                "this_month": _money(month_pay_by_school[sid]),
            }
        )
    out.sort(key=lambda r: r["total_paid"], reverse=(sort != "low"))
    return JsonResponse({"ok": True, "schools": out})


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def analytics_payment_status_api(request):
    connection.set_schema_to_public()
    school_ids = _analytics_school_ids(request)
    if school_ids is not None and len(school_ids) == 0:
        return JsonResponse(
            {"ok": True, "labels": ["Paid", "Pending", "Overdue", "Free period"], "values": [0, 0, 0, 0]}
        )

    inv_qs = _invoice_qs(school_ids)
    today = timezone.localdate()

    n_paid = inv_qs.filter(status=SchoolGeneratedInvoice.Status.PAID).count()
    issued = inv_qs.filter(status=SchoolGeneratedInvoice.Status.ISSUED)
    n_overdue = issued.filter(due_date__lt=today).count()
    n_pending = issued.filter(Q(due_date__isnull=True) | Q(due_date__gte=today)).count()

    sch_qs = School.objects.exclude(schema_name="public")
    if school_ids is not None:
        sch_qs = sch_qs.filter(pk__in=school_ids)
    n_free = sum(1 for s in sch_qs if _school_in_free_period(s, today))

    return JsonResponse(
        {
            "ok": True,
            "labels": ["Paid invoices", "Pending", "Overdue", "Schools in free period"],
            "values": [n_paid, n_pending, n_overdue, n_free],
        }
    )


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def analytics_growth_api(request):
    connection.set_schema_to_public()
    school_ids = _analytics_school_ids(request)
    today = timezone.localdate()
    first_this = today.replace(day=1)
    first_prev = (first_this - timedelta(days=1)).replace(day=1)
    end_prev = first_this - timedelta(days=1)

    sch_all = School.objects.exclude(schema_name="public")
    if school_ids is not None:
        sch_all = sch_all.filter(pk__in=school_ids) if school_ids else sch_all.none()

    new_this = sch_all.filter(created_on__date__gte=first_this).count()
    new_prev = sch_all.filter(
        created_on__date__gte=first_prev, created_on__date__lte=end_prev
    ).count()

    pay_qs = _payment_qs(school_ids)
    rev_this = pay_qs.filter(payment_date__gte=first_this).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    rev_prev = pay_qs.filter(
        payment_date__gte=first_prev, payment_date__lte=end_prev
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    rev_mom = None
    if rev_prev > 0:
        rev_mom = float(((rev_this - rev_prev) / rev_prev) * 100)

    delays = []
    inv_q = SchoolGeneratedInvoice.objects.filter(
        status=SchoolGeneratedInvoice.Status.PAID,
        paid_at__isnull=False,
        due_date__isnull=False,
    )
    if school_ids is not None:
        inv_q = inv_q.filter(school_id__in=school_ids)
    for inv in inv_q[:800]:
        delays.append((inv.paid_at.date() - inv.due_date).days)
    avg_delay = round(sum(delays) / len(delays), 1) if delays else None

    delay_trend_labels = []
    delay_trend_values = []
    for yy, mm in _last_n_calendar_months(6):
        sub = []
        for inv in SchoolGeneratedInvoice.objects.filter(
            status=SchoolGeneratedInvoice.Status.PAID,
            paid_at__isnull=False,
            due_date__isnull=False,
            paid_at__year=yy,
            paid_at__month=mm,
        )[:400]:
            if school_ids is not None and inv.school_id not in school_ids:
                continue
            sub.append((inv.paid_at.date() - inv.due_date).days)
        delay_trend_labels.append(date(yy, mm, 1).strftime("%b %y"))
        delay_trend_values.append(round(sum(sub) / len(sub), 1) if sub else 0.0)

    return JsonResponse(
        {
            "ok": True,
            "new_schools_this_month": new_this,
            "new_schools_last_month": new_prev,
            "revenue_mom_pct": round(rev_mom, 1) if rev_mom is not None else None,
            "student_mom_pct": None,
            "avg_payment_delay_days": avg_delay,
            "delay_trend": {"labels": delay_trend_labels, "values": delay_trend_values},
        }
    )


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def analytics_month_collection_api(request):
    connection.set_schema_to_public()
    school_ids = _analytics_school_ids(request)
    y, m = _parse_month_param(request.GET.get("month"))
    mk = _month_key(y, m)
    d_start, d_end = _month_bounds(y, m)

    inv_qs = _invoice_qs(school_ids)
    expected = inv_qs.filter(invoice_month_key=mk).aggregate(s=Sum("grand_total"))["s"] or Decimal("0")

    pay_qs = _payment_qs(school_ids)
    collected = pay_qs.filter(payment_date__gte=d_start, payment_date__lte=d_end).aggregate(
        s=Sum("amount")
    )["s"] or Decimal("0")

    remaining = expected - collected
    if remaining < 0:
        remaining = Decimal("0")
    pct = 0.0
    if expected > 0:
        pct = float(min(100.0, (collected / expected) * 100))

    return JsonResponse(
        {
            "ok": True,
            "month_label": date(y, m, 1).strftime("%B %Y"),
            "month_key": mk,
            "expected": _money(expected),
            "collected": _money(collected),
            "remaining": _money(remaining),
            "progress_pct": round(pct, 1),
        }
    )


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def analytics_plan_distribution_api(request):
    connection.set_schema_to_public()
    school_ids = _analytics_school_ids(request)
    qs = School.objects.exclude(schema_name="public")
    if school_ids is not None:
        qs = qs.filter(pk__in=school_ids) if school_ids else qs.none()

    buckets = {"Basic": 0, "Pro": 0, "Enterprise": 0, "No plan": 0}
    for s in qs.select_related("plan"):
        if not s.plan_id:
            buckets["No plan"] += 1
        elif s.plan.name == PlanName.BASIC:
            buckets["Basic"] += 1
        elif s.plan.name == PlanName.PRO:
            buckets["Pro"] += 1
        elif s.plan.name == PlanName.PREMIUM:
            buckets["Enterprise"] += 1
        else:
            buckets["No plan"] += 1

    return JsonResponse(
        {
            "ok": True,
            "labels": list(buckets.keys()),
            "values": list(buckets.values()),
        }
    )


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def analytics_top_and_risk_api(request):
    connection.set_schema_to_public()
    school_ids = _analytics_school_ids(request)
    sch_base = School.objects.exclude(schema_name="public").select_related("plan")
    if school_ids is not None:
        sch_base = sch_base.filter(pk__in=school_ids) if school_ids else sch_base.none()

    id_list = list(sch_base.values_list("id", flat=True))
    if not id_list:
        return JsonResponse({"ok": True, "top_schools": [], "at_risk": []})

    pay_by = defaultdict(lambda: Decimal("0"))
    for row in SaaSPlatformPayment.objects.filter(school_id__in=id_list).values("school_id").annotate(
        t=Sum("amount")
    ):
        pay_by[row["school_id"]] = row["t"] or Decimal("0")

    overdue_by = defaultdict(lambda: Decimal("0"))
    today = timezone.localdate()
    for row in (
        SchoolGeneratedInvoice.objects.filter(
            school_id__in=id_list,
            status=SchoolGeneratedInvoice.Status.ISSUED,
            due_date__lt=today,
        )
        .values("school_id")
        .annotate(t=Sum("grand_total"))
    ):
        overdue_by[row["school_id"]] = row["t"] or Decimal("0")

    ranked = []
    for s in sch_base:
        stu, _ = _safe_tenant_counts(s)
        ranked.append(
            {
                "id": s.pk,
                "name": s.name,
                "total_paid": _money(pay_by[s.pk]),
                "overdue": _money(overdue_by[s.pk]),
                "status": s.school_status,
                "students": int(stu),
                "low_activity": int(stu) == 0
                and s.school_status
                in (School.SchoolStatus.ACTIVE, School.SchoolStatus.TRIAL),
            }
        )
    top5 = sorted(ranked, key=lambda r: r["total_paid"], reverse=True)[:5]
    at_risk = sorted(
        [
            r
            for r in ranked
            if r["overdue"] > 0
            or r["status"] == School.SchoolStatus.SUSPENDED
            or r.get("low_activity")
        ],
        key=lambda r: (r["overdue"], 1 if r.get("low_activity") else 0),
        reverse=True,
    )[:8]

    return JsonResponse({"ok": True, "top_schools": top5, "at_risk": at_risk})


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def analytics_export_csv(request):
    connection.set_schema_to_public()
    school_ids = _analytics_school_ids(request)
    y, m = _parse_month_param(request.GET.get("month"))
    d_start, d_end = _month_bounds(y, m)
    mk = _month_key(y, m)

    sch_base = School.objects.exclude(schema_name="public").select_related("plan").order_by("name")
    if school_ids is not None:
        sch_base = sch_base.filter(pk__in=school_ids) if school_ids else sch_base.none()

    id_list = list(sch_base.values_list("id", flat=True))
    pay_by = defaultdict(lambda: Decimal("0"))
    pending_by = defaultdict(lambda: Decimal("0"))
    month_by = defaultdict(lambda: Decimal("0"))
    if id_list:
        for row in SaaSPlatformPayment.objects.filter(school_id__in=id_list).values("school_id").annotate(
            t=Sum("amount")
        ):
            pay_by[row["school_id"]] = row["t"] or Decimal("0")
        for row in (
            SchoolGeneratedInvoice.objects.filter(
                school_id__in=id_list,
                status=SchoolGeneratedInvoice.Status.ISSUED,
            )
            .values("school_id")
            .annotate(t=Sum("grand_total"))
        ):
            pending_by[row["school_id"]] = row["t"] or Decimal("0")
        for row in SaaSPlatformPayment.objects.filter(
            school_id__in=id_list,
            payment_date__gte=d_start,
            payment_date__lte=d_end,
        ).values("school_id").annotate(t=Sum("amount")):
            month_by[row["school_id"]] = row["t"] or Decimal("0")

    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "school_id",
            "name",
            "status",
            "plan",
            "students",
            "total_paid",
            "pending_invoices",
            f"collected_{mk}",
        ]
    )
    for s in sch_base:
        stu, _ = _safe_tenant_counts(s)
        plan = "No plan"
        if s.plan_id:
            plan = s.plan.get_name_display()
        w.writerow(
            [
                s.pk,
                s.name,
                s.school_status,
                plan,
                stu,
                _money(pay_by[s.pk]),
                _money(pending_by[s.pk]),
                _money(month_by[s.pk]),
            ]
        )
    resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="analytics_schools_{mk}.csv"'
    return resp
