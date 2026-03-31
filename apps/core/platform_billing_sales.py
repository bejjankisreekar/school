"""
Superadmin sales / collections view: per-school subscription amount due vs collected vs outstanding.

Rules (practical SaaS):
- **Monthly amount due** = active tier price-per-student × enrolled students (same basis as MRR).
- Trial / no plan / setup: due = 0 (shown clearly).
- **Collected** = sum of `SaaSPlatformPayment` in the selected calendar month (or YTD / all-time columns).
- **Outstanding (month)** = max(0, due − collected in that month). Overpayments show as advance credit.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Sum

from apps.customers.models import SaaSPlatformPayment, School

from .platform_financials import (
    _billing_row_for_school,
    _safe_tenant_footprint,
)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


@dataclass
class SchoolBillingSalesRow:
    """One row for the billing sales table."""

    school_id: int
    code: str
    name: str
    contact_person: str
    contact_email: str
    phone: str
    plan_name: str
    price_per_student: Decimal | None
    billing_cycle: str
    student_count: int
    monthly_amount_due: Decimal
    status_key: str
    status_label: str
    trial_ends: date | None
    paid_in_month: Decimal
    paid_ytd: Decimal
    paid_all_time: Decimal
    outstanding_month: Decimal
    advance_month: Decimal
    collection_pct_month: int | None  # 0–100 when due > 0


def _payment_sums_by_school(
    year: int,
    month: int,
) -> tuple[dict[int, Decimal], dict[int, Decimal], dict[int, Decimal]]:
    """Returns (paid_in_selected_month, paid_ytd_through_month_end, paid_all_time) per school_id."""
    month_start, month_end = _month_bounds(year, month)
    ytd_start = date(year, 1, 1)

    month_qs = (
        SaaSPlatformPayment.objects.filter(
            payment_date__gte=month_start,
            payment_date__lte=month_end,
        )
        .values("school_id")
        .annotate(s=Sum("amount"))
    )
    paid_month: dict[int, Decimal] = {
        r["school_id"]: (r["s"] or Decimal("0")).quantize(Decimal("0.01")) for r in month_qs
    }

    ytd_qs = (
        SaaSPlatformPayment.objects.filter(
            payment_date__gte=ytd_start,
            payment_date__lte=month_end,
        )
        .values("school_id")
        .annotate(s=Sum("amount"))
    )
    paid_ytd: dict[int, Decimal] = {
        r["school_id"]: (r["s"] or Decimal("0")).quantize(Decimal("0.01")) for r in ytd_qs
    }

    all_qs = SaaSPlatformPayment.objects.values("school_id").annotate(s=Sum("amount"))
    paid_all: dict[int, Decimal] = {
        r["school_id"]: (r["s"] or Decimal("0")).quantize(Decimal("0.01")) for r in all_qs
    }

    return paid_month, paid_ytd, paid_all


def build_billing_sales_rows(year: int, month: int) -> list[SchoolBillingSalesRow]:
    today = date.today()
    paid_month, paid_ytd, paid_all = _payment_sums_by_school(year, month)

    schools = list(
        School.objects.exclude(schema_name="public")
        .select_related("saas_plan", "plan")
        .order_by("name")
    )
    rows: list[SchoolBillingSalesRow] = []

    for school in schools:
        _, n_students, _ = _safe_tenant_footprint(school)
        br = _billing_row_for_school(school, n_students, today)
        monthly_due = br.estimated_mrr
        if br.status_key != "paying":
            monthly_due = Decimal("0")

        pm = paid_month.get(school.pk, Decimal("0"))
        py = paid_ytd.get(school.pk, Decimal("0"))
        pa = paid_all.get(school.pk, Decimal("0"))

        gap = monthly_due - pm
        outstanding = max(Decimal("0"), gap)
        advance = max(Decimal("0"), -gap)

        pct: int | None = None
        if monthly_due > 0:
            pct = int((min(pm / monthly_due, Decimal("1")) * 100).quantize(Decimal("1")))

        plan = school.saas_plan
        price_ps = plan.price_per_student if plan else None
        bc = plan.get_billing_cycle_display() if plan else "—"

        rows.append(
            SchoolBillingSalesRow(
                school_id=school.pk,
                code=school.code,
                name=school.name,
                contact_person=(school.contact_person or "").strip(),
                contact_email=(school.contact_email or "").strip(),
                phone=(school.phone or "").strip(),
                plan_name=br.plan_tier,
                price_per_student=price_ps,
                billing_cycle=bc,
                student_count=n_students,
                monthly_amount_due=monthly_due,
                status_key=br.status_key,
                status_label=br.status_label,
                trial_ends=br.trial_ends,
                paid_in_month=pm,
                paid_ytd=py,
                paid_all_time=pa,
                outstanding_month=outstanding,
                advance_month=advance,
                collection_pct_month=pct,
            )
        )
    return rows


def summarize_billing_sales(rows: list[SchoolBillingSalesRow]) -> dict:
    """Totals for summary cards."""
    due = sum((r.monthly_amount_due for r in rows), Decimal("0"))
    coll = sum((r.paid_in_month for r in rows), Decimal("0"))
    out = sum((r.outstanding_month for r in rows), Decimal("0"))
    adv = sum((r.advance_month for r in rows), Decimal("0"))
    ytd = sum((r.paid_ytd for r in rows), Decimal("0"))
    all_t = sum((r.paid_all_time for r in rows), Decimal("0"))
    paying_n = sum(1 for r in rows if r.status_key == "paying")
    return {
        "total_monthly_due": due.quantize(Decimal("0.01")),
        "total_collected_month": coll.quantize(Decimal("0.01")),
        "total_outstanding_month": out.quantize(Decimal("0.01")),
        "total_advance_month": adv.quantize(Decimal("0.01")),
        "total_ytd": ytd.quantize(Decimal("0.01")),
        "total_all_time": all_t.quantize(Decimal("0.01")),
        "count_paying_schools": paying_n,
        "collection_rate_pct": int((min(coll / due, Decimal("1")) * 100).quantize(Decimal("1")))
        if due > 0
        else None,
    }
