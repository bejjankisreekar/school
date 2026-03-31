"""
Platform SaaS billing: monthly invoices, payments against invoices, PDF receipts.
"""
from __future__ import annotations

import uuid
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.pdf_utils import render_pdf_bytes
from apps.core.platform_financials import _safe_tenant_footprint

from .models import (
    PlatformBillingReceipt,
    PlatformInvoice,
    PlatformInvoicePayment,
    School,
    SchoolSubscription,
)


def _month_last_day(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _compute_amounts_for_school(school: School) -> tuple[SchoolSubscription | None, int, Decimal, Decimal, Decimal, Decimal]:
    """
    Returns (current_subscription, students_count, price_per_student, gross, discount, final).
    """
    sub = (
        SchoolSubscription.objects.filter(school=school, is_current=True)
        .select_related("plan")
        .first()
    )
    _, n_students, _ = _safe_tenant_footprint(school)
    if not sub or not sub.plan:
        return sub, n_students, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
    price = Decimal(sub.plan.price_per_student or 0).quantize(Decimal("0.01"))
    gross = (price * n_students).quantize(Decimal("0.01"))
    discount = Decimal("0")
    final = (gross - discount).quantize(Decimal("0.01"))
    return sub, n_students, price, gross, discount, final


def ensure_invoice_for_period(
    school: School,
    year: int,
    month: int,
    *,
    force_refresh_amounts: bool = False,
) -> tuple[PlatformInvoice, bool]:
    """
    Create a monthly invoice for the school if it does not exist.
    If force_refresh_amounts and invoice is still pending, refresh line amounts from current plan/students.
    Returns (invoice, created).
    """
    if month < 1 or month > 12:
        raise ValueError("month must be 1–12")

    existing = PlatformInvoice.objects.filter(school=school, year=year, month=month).first()
    if existing and not force_refresh_amounts:
        return existing, False

    sub, n_students, price, gross, discount, final = _compute_amounts_for_school(school)
    due = _month_last_day(year, month)

    if existing:
        if existing.status != PlatformInvoice.Status.PENDING:
            return existing, False
        existing.subscription = sub
        existing.students_count = n_students
        existing.price_per_student = price
        existing.gross_amount = gross
        existing.discount_amount = discount
        existing.final_amount = final
        existing.due_date = due
        existing.save()
        if existing.final_amount == 0:
            existing.status = PlatformInvoice.Status.PAID
            existing.save(update_fields=["status"])
        return existing, False

    temp_key = f"TMP-{uuid.uuid4().hex}"
    with transaction.atomic():
        inv = PlatformInvoice(
            school=school,
            subscription=sub,
            year=year,
            month=month,
            students_count=n_students,
            price_per_student=price,
            gross_amount=gross,
            discount_amount=discount,
            final_amount=final,
            status=PlatformInvoice.Status.PENDING,
            due_date=due,
            invoice_number=temp_key,
        )
        inv.save()
        inv.invoice_number = f"INV-{year}{month:02d}-{inv.pk:06d}"
        inv.save(update_fields=["invoice_number"])
        if inv.final_amount == 0:
            inv.status = PlatformInvoice.Status.PAID
            inv.save(update_fields=["status"])
        return inv, True


def total_paid_for_invoice(invoice: PlatformInvoice) -> Decimal:
    s = invoice.invoice_payments.aggregate(t=Sum("amount_paid"))["t"]
    return (s or Decimal("0")).quantize(Decimal("0.01"))


def _recompute_invoice_status(invoice: PlatformInvoice) -> None:
    total = total_paid_for_invoice(invoice)
    final = invoice.final_amount
    if total <= 0:
        invoice.status = PlatformInvoice.Status.PENDING
    elif total < final:
        invoice.status = PlatformInvoice.Status.PARTIAL
    else:
        invoice.status = PlatformInvoice.Status.PAID
    invoice.save(update_fields=["status"])


def _render_receipt_pdf_bytes(payment: PlatformInvoicePayment, receipt: PlatformBillingReceipt) -> bytes | None:
    inv = payment.invoice
    school = payment.school
    mode_display = payment.get_payment_mode_display()
    return render_pdf_bytes(
        "pdf/saas_payment_receipt.html",
        {
            "receipt_number": receipt.receipt_number,
            "school_name": school.name,
            "school_code": school.code,
            "invoice_ref": inv.invoice_number,
            "invoice_period": f"{inv.month:02d}/{inv.year}",
            "paid_amount": payment.amount_paid,
            "paid_on": payment.paid_on,
            "payment_mode": mode_display,
            "transaction_id": payment.transaction_id or "—",
            "generated_on": receipt.generated_on,
        },
    )


@transaction.atomic
def record_invoice_payment(
    *,
    invoice: PlatformInvoice,
    amount_paid: Decimal,
    payment_mode: str,
    transaction_id: str,
    user,
    paid_on: datetime | None = None,
) -> tuple[PlatformInvoicePayment, PlatformBillingReceipt]:
    """
    Record a payment against an invoice, update status, create receipt row and PDF in storage.
    """
    if amount_paid <= Decimal("0"):
        raise ValueError("Amount must be greater than zero.")

    paid_on = paid_on or timezone.now()
    school = invoice.school

    pay = PlatformInvoicePayment.objects.create(
        school=school,
        invoice=invoice,
        amount_paid=amount_paid.quantize(Decimal("0.01")),
        payment_mode=payment_mode,
        transaction_id=(transaction_id or "").strip()[:200],
        paid_on=paid_on,
        recorded_by=user,
    )

    _recompute_invoice_status(invoice)

    receipt = PlatformBillingReceipt.objects.create(
        payment=pay,
        receipt_number=f"REC-{paid_on.year}{paid_on.month:02d}-{pay.pk:06d}",
    )

    pdf_bytes = _render_receipt_pdf_bytes(pay, receipt)
    if pdf_bytes:
        rel_path = f"saas_receipts/{paid_on.year}/{receipt.receipt_number.replace('/', '-')}.pdf"
        default_storage.save(rel_path, ContentFile(pdf_bytes))
        receipt.pdf_url = rel_path
        receipt.save(update_fields=["pdf_url"])

    return pay, receipt
