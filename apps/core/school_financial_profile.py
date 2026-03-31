"""
Aggregate billing, invoices, payments, and subscription data for superadmin school financial view.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Sum

from apps.customers.billing_engine import total_paid_for_invoice
from apps.customers.models import (
    PlatformInvoice,
    PlatformInvoicePayment,
    SaaSPlatformPayment,
    School,
    SchoolSubscription,
)


@dataclass
class InvoiceRowExtra:
    """Template-friendly invoice with balance."""

    invoice: PlatformInvoice
    paid: Decimal
    remaining: Decimal


def _month_bounds(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])


def _iter_months_back(n: int, end: date) -> list[tuple[int, int]]:
    """Chronological list of (year, month) covering last n months including end's month."""
    y, m = end.year, end.month
    stack: list[tuple[int, int]] = []
    for _ in range(n):
        stack.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(stack))


def build_school_financial_profile(
    school: School,
    *,
    invoice_status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """
    Returns context dict for superadmin school financial detail template.
    """
    today = date.today()
    live_students = _live_student_count(school)

    sub_current = (
        SchoolSubscription.objects.filter(school=school, is_current=True)
        .select_related("plan", "coupon")
        .first()
    )

    # --- Invoices (table + KPIs; period filter applied in Python) ---
    inv_qs = (
        PlatformInvoice.objects.filter(school=school)
        .select_related("subscription", "subscription__plan")
        .order_by("-year", "-month")
    )
    inv_list = list(inv_qs)
    if invoice_status and invoice_status in ("pending", "partial", "paid"):
        inv_list = [i for i in inv_list if i.status == invoice_status]

    df = date_from or date(2000, 1, 1)
    dt = date_to or date(2100, 12, 31)
    df_ym, dt_ym = df.year * 100 + df.month, dt.year * 100 + dt.month

    def _in_period(inv: PlatformInvoice) -> bool:
        v = inv.year * 100 + inv.month
        return df_ym <= v <= dt_ym

    inv_list = [i for i in inv_list if _in_period(i)]

    invoice_rows: list[InvoiceRowExtra] = []
    total_billed = Decimal("0")
    total_discounts_on_invoices = Decimal("0")
    for inv in inv_list:
        paid = total_paid_for_invoice(inv)
        rem = (inv.final_amount - paid).quantize(Decimal("0.01"))
        total_billed += inv.final_amount
        total_discounts_on_invoices += inv.discount_amount
        invoice_rows.append(InvoiceRowExtra(invoice=inv, paid=paid, remaining=rem))

    # Outstanding from all invoices (not only filtered display) for KPIs
    all_inv = PlatformInvoice.objects.filter(school=school)
    outstanding = Decimal("0")
    overdue_count = 0
    aging = {"0_30": Decimal("0"), "30_60": Decimal("0"), "60_plus": Decimal("0")}
    for inv in all_inv:
        paid = total_paid_for_invoice(inv)
        rem = (inv.final_amount - paid).quantize(Decimal("0.01"))
        if rem <= 0:
            continue
        outstanding += rem
        if inv.status != PlatformInvoice.Status.PAID and inv.due_date < today:
            overdue_count += 1
            days = (today - inv.due_date).days
            if days <= 30:
                aging["0_30"] += rem
            elif days <= 60:
                aging["30_60"] += rem
            else:
                aging["60_plus"] += rem

    # Payments (invoice-linked)
    inv_payments = (
        PlatformInvoicePayment.objects.filter(school=school)
        .select_related("invoice", "recorded_by", "billing_receipt")
        .order_by("-paid_on")
    )
    total_paid_invoice_linked = (
        PlatformInvoicePayment.objects.filter(school=school).aggregate(s=Sum("amount_paid"))["s"] or Decimal("0")
    )

    # Legacy SaaS ledger payments
    legacy_payments = (
        SaaSPlatformPayment.objects.filter(school=school)
        .select_related("recorded_by", "subscription")
        .order_by("-payment_date", "-id")
    )
    total_paid_legacy = legacy_payments.aggregate(s=Sum("amount"))["s"] or Decimal("0")

    last_pay_dates: list[date] = []
    lp = legacy_payments.first()
    if lp:
        last_pay_dates.append(lp.payment_date)
    ipp = inv_payments.first()
    if ipp:
        last_pay_dates.append(ipp.paid_on.date())
    last_payment_date = max(last_pay_dates) if last_pay_dates else None

    total_paid_combined = (total_paid_invoice_linked + total_paid_legacy).quantize(Decimal("0.01"))

    # Cash net vs all invoices (legacy payments are not always linked to invoice rows — avoids
    # "paid 300, billed 237, outstanding still 237" when cash was recorded only on the ledger.)
    total_invoiced_lifetime = (
        PlatformInvoice.objects.filter(school=school).aggregate(s=Sum("final_amount"))["s"] or Decimal("0")
    )
    total_invoiced_lifetime = total_invoiced_lifetime.quantize(Decimal("0.01"))
    net_outstanding = max(
        Decimal("0"), (total_invoiced_lifetime - total_paid_combined).quantize(Decimal("0.01"))
    )
    advance_credit = max(
        Decimal("0"), (total_paid_combined - total_invoiced_lifetime).quantize(Decimal("0.01"))
    )

    # Charts: last 12 months
    months_12 = _iter_months_back(12, today)
    labels: list[str] = []
    series_billed: list[float] = []
    series_paid: list[float] = []
    for y, m in months_12:
        labels.append(f"{date(y, m, 1).strftime('%b %y')}")
        b = (
            PlatformInvoice.objects.filter(school=school, year=y, month=m).aggregate(
                s=Sum("final_amount")
            )["s"]
            or Decimal("0")
        )
        ms, me = _month_bounds(y, m)
        legacy_m = SaaSPlatformPayment.objects.filter(
            school=school, payment_date__gte=ms, payment_date__lte=me
        ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
        ip_m = PlatformInvoicePayment.objects.filter(
            school=school, paid_on__year=y, paid_on__month=m
        ).aggregate(s=Sum("amount_paid"))["s"] or Decimal("0")
        series_billed.append(float(b))
        series_paid.append(float(legacy_m + ip_m))

    # Pie: invoice status by count
    status_counts = {k: 0 for k in ("pending", "partial", "paid")}
    for inv in PlatformInvoice.objects.filter(school=school):
        status_counts[inv.status] = status_counts.get(inv.status, 0) + 1

    # Bar: paid vs net outstanding (cash basis), not sum of invoice lines (avoids double-count skew)
    pending_amt = net_outstanding
    paid_amt = total_paid_combined

    # Coupons: all subscription rows with coupon
    coupon_history = (
        SchoolSubscription.objects.filter(school=school)
        .exclude(coupon__isnull=True)
        .select_related("coupon", "plan")
        .order_by("-start_date")[:20]
    )

    # Subscription list for "free months" — only current snapshot in DB
    sub_snapshots = (
        SchoolSubscription.objects.filter(school=school).select_related("plan", "coupon").order_by("-start_date")[:10]
    )

    plan = school.saas_plan
    price_ps = plan.price_per_student if plan else None
    billing_cycle = plan.get_billing_cycle_display() if plan else "—"

    status_label = school.get_school_status_display()

    return {
        "school": school,
        "live_students": live_students,
        "status_label": status_label,
        "sub_current": sub_current,
        "plan": plan,
        "price_per_student": price_ps,
        "billing_cycle": billing_cycle,
        "subscription_start": sub_current.start_date if sub_current else None,
        "subscription_end": sub_current.end_date if sub_current else None,
        "invoice_rows": invoice_rows,
        "invoices_all_count": PlatformInvoice.objects.filter(school=school).count(),
        "total_billed": total_billed.quantize(Decimal("0.01")),
        "total_discounts_on_invoices": total_discounts_on_invoices.quantize(Decimal("0.01")),
        "total_paid_invoice_linked": total_paid_invoice_linked.quantize(Decimal("0.01")),
        "total_paid_legacy": total_paid_legacy.quantize(Decimal("0.01")),
        "total_paid_combined": total_paid_combined,
        "total_invoiced_lifetime": total_invoiced_lifetime,
        "outstanding": net_outstanding,
        "invoice_remaining_sum": outstanding.quantize(Decimal("0.01")),
        "advance_credit": advance_credit,
        "overdue_count": overdue_count,
        "aging_0_30": aging["0_30"].quantize(Decimal("0.01")),
        "aging_30_60": aging["30_60"].quantize(Decimal("0.01")),
        "aging_60_plus": aging["60_plus"].quantize(Decimal("0.01")),
        "last_payment_date": last_payment_date,
        "legacy_payments": legacy_payments[:50],
        "invoice_payments": inv_payments[:50],
        "chart": {
            "labels": labels,
            "billed": series_billed,
            "paid": series_paid,
            "pie_labels": ["Pending", "Partial", "Paid"],
            "pie_data": [
                status_counts.get("pending", 0),
                status_counts.get("partial", 0),
                status_counts.get("paid", 0),
            ],
            "bar_paid": float(paid_amt),
            "bar_pending": float(pending_amt),
        },
        "coupon_history": coupon_history,
        "sub_snapshots": sub_snapshots,
        "net_revenue_estimate": (total_paid_combined - total_discounts_on_invoices).quantize(Decimal("0.01")),
        "today": today,
        "invoice_status": invoice_status or "",
        "date_from": date_from,
        "date_to": date_to,
    }


def _live_student_count(school: School) -> int:
    from apps.core.platform_financials import _safe_tenant_footprint

    _, n, _ = _safe_tenant_footprint(school)
    return n
