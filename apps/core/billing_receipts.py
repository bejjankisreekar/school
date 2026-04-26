"""HTML/PDF context builders for fee receipts and student fee invoices."""

from __future__ import annotations

import base64
import mimetypes
from decimal import Decimal
from io import BytesIO
from typing import TYPE_CHECKING, Any

from django.db import connection
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

if TYPE_CHECKING:

    from apps.customers.models import School
    from apps.school_data.models import AcademicYear, Payment, PaymentBatch, Student


def get_tenant_school() -> School | None:
    return getattr(connection, "tenant", None)


def school_branding(school: School | None) -> dict[str, Any]:
    if school is None:
        return {
            "name": "School",
            "address": "",
            "phone": "",
            "email": "",
            "website": "",
            "logo_data_uri": None,
            "logo_url": None,
        }
    logo_data_uri = None
    logo_url = None
    if getattr(school, "logo", None) and school.logo:
        try:
            logo_url = school.logo.url
        except Exception:
            logo_url = None
        try:
            path = school.logo.path
            mime = mimetypes.guess_type(path)[0] or "image/png"
            with open(path, "rb") as f:
                logo_data_uri = f"data:{mime};base64,{base64.b64encode(f.read()).decode('ascii')}"
        except Exception:
            logo_data_uri = None
    return {
        "name": school.name,
        "address": (school.address or "").strip(),
        "phone": (school.phone or "").strip(),
        "email": (school.contact_email or "").strip(),
        "website": (school.website or "").strip(),
        "logo_data_uri": logo_data_uri,
        "logo_url": logo_url,
    }


def _school_branding_absolute_logo(
    brand: dict[str, Any], request: HttpRequest | None
) -> dict[str, Any]:
    """Prefer embedded logo for PDF; otherwise make logo_url absolute for same-origin browser use."""
    if not request:
        return brand
    url = brand.get("logo_url")
    if url and url.startswith("/"):
        try:
            return {**brand, "logo_url": request.build_absolute_uri(url)}
        except Exception:
            return brand
    return brand


def _qr_data_uri(text: str) -> str | None:
    try:
        import qrcode
    except ImportError:
        return None
    img = qrcode.make(text, box_size=3, border=1)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def receipt_batch_context(
    batch: PaymentBatch,
    *,
    request: HttpRequest | None = None,
) -> dict[str, Any]:
    school = get_tenant_school()
    brand = _school_branding_absolute_logo(school_branding(school), request)

    lines = []
    for lp in batch.line_payments.select_related("fee__fee_structure__fee_type").order_by(
        "fee__fee_structure__fee_type__name", "id"
    ):
        ft = lp.fee.fee_structure.fee_type
        lines.append(
            {
                "fee_type_name": ft.name if ft else "—",
                "amount": lp.amount,
            }
        )

    tender_rows: list[dict[str, Any]] = []
    for t in batch.tenders.order_by("id"):
        tender_rows.append(
            {
                "payment_method": t.payment_method,
                "amount": t.amount,
                "transaction_reference": t.transaction_reference or "",
            }
        )
    if not tender_rows:
        tender_rows = [
            {
                "payment_method": batch.payment_method,
                "amount": batch.total_amount,
                "transaction_reference": batch.transaction_reference or "",
            }
        ]

    student = batch.student
    st_user = student.user
    name = st_user.get_full_name() or st_user.username
    class_name = student.classroom.name if student.classroom_id else "—"
    sec_name = student.section.name if student.section_id else "—"

    ay = batch.academic_year
    bundle = None
    total_fees = total_paid = total_pending = total_discount = Decimal("0")
    if ay:
        from apps.core import fee_services

        bundle = fee_services.build_fee_collect_bundle(student, ay)
        total_fees = bundle["total_final"]
        total_paid = bundle["total_paid_sum"]
        total_pending = bundle["total_balance_sum"]
        total_discount = bundle["total_discount"]

    receipt_no = (batch.receipt_code or "").strip() or f"RCPT-{batch.payment_date.year}-{batch.pk:06d}"

    verify_url = ""
    if request:
        try:
            verify_url = request.build_absolute_uri(
                reverse("core:billing_receipt_batch", args=[batch.pk])
            )
        except Exception:
            verify_url = ""

    qr_uri = _qr_data_uri(verify_url) if verify_url else None

    collected_by = ""
    if batch.received_by_id:
        u = batch.received_by
        collected_by = u.get_full_name() or u.username if u else ""

    return {
        "is_pdf": False,
        "school": brand,
        "document": {"number": receipt_no, "date": batch.payment_date},
        "doc_subtitle": "Official payment acknowledgement",
        "receipt_title": "PAYMENT RECEIPT",
        "receipt_number": receipt_no,
        "internal_receipt_note": (batch.receipt_number or "").strip(),
        "payment_date": batch.payment_date,
        "batch": batch,
        "lines": lines,
        "tenders": tender_rows,
        "total_paid": batch.total_amount,
        "student_name": name,
        "student_class": class_name,
        "student_section": sec_name,
        "admission_number": (student.admission_number or "").strip() or "—",
        "academic_year": ay,
        "summary_total_fees": total_fees,
        "summary_total_paid": total_paid,
        "summary_total_pending": total_pending,
        "summary_total_discount": total_discount,
        "bundle": bundle,
        "collected_by": collected_by,
        "verify_url": verify_url,
        "qr_data_uri": qr_uri,
    }


def receipt_orphan_payment_context(
    payment: Payment,
    *,
    request: HttpRequest | None = None,
) -> dict[str, Any]:
    """Single Payment row with no batch (legacy / one-line)."""
    school = get_tenant_school()
    brand = _school_branding_absolute_logo(school_branding(school), request)
    fee = payment.fee
    ft = fee.fee_structure.fee_type
    lines = [{"fee_type_name": ft.name if ft else "—", "amount": payment.amount}]

    tender_rows = [
        {
            "payment_method": payment.payment_method,
            "amount": payment.amount,
            "transaction_reference": payment.transaction_reference or "",
        }
    ]

    student = fee.student
    st_user = student.user
    name = st_user.get_full_name() or st_user.username
    class_name = student.classroom.name if student.classroom_id else "—"
    sec_name = student.section.name if student.section_id else "—"
    ay = fee.academic_year

    total_fees = total_paid = total_pending = total_discount = Decimal("0")
    if ay:
        from apps.core import fee_services

        bundle = fee_services.build_fee_collect_bundle(student, ay)
        total_fees = bundle["total_final"]
        total_paid = bundle["total_paid_sum"]
        total_pending = bundle["total_balance_sum"]
        total_discount = bundle["total_discount"]
    else:
        bundle = None

    receipt_no = f"RCPT-{payment.payment_date.year}-P{payment.pk:06d}"

    verify_url = ""
    if request:
        try:
            verify_url = request.build_absolute_uri(
                reverse("core:billing_receipt_payment", args=[payment.pk])
            )
        except Exception:
            verify_url = ""

    qr_uri = _qr_data_uri(verify_url) if verify_url else None

    collected_by = ""
    if payment.received_by_id:
        u = payment.received_by
        collected_by = u.get_full_name() or u.username if u else ""

    return {
        "is_pdf": False,
        "school": brand,
        "document": {"number": receipt_no, "date": payment.payment_date},
        "doc_subtitle": "Official payment acknowledgement",
        "receipt_title": "PAYMENT RECEIPT",
        "receipt_number": receipt_no,
        "internal_receipt_note": (payment.receipt_number or "").strip(),
        "payment_date": payment.payment_date,
        "batch": None,
        "payment": payment,
        "lines": lines,
        "tenders": tender_rows,
        "total_paid": payment.amount,
        "student_name": name,
        "student_class": class_name,
        "student_section": sec_name,
        "admission_number": (student.admission_number or "").strip() or "—",
        "academic_year": ay,
        "summary_total_fees": total_fees,
        "summary_total_paid": total_paid,
        "summary_total_pending": total_pending,
        "summary_total_discount": total_discount,
        "bundle": bundle,
        "collected_by": collected_by,
        "verify_url": verify_url,
        "qr_data_uri": qr_uri,
    }


def student_invoice_context(
    student: Student,
    academic_year: AcademicYear | None,
    *,
    request: HttpRequest | None = None,
) -> dict[str, Any]:
    from apps.core import fee_services

    school = get_tenant_school()
    brand = _school_branding_absolute_logo(school_branding(school), request)
    bundle = fee_services.build_fee_collect_bundle(student, academic_year)

    rows = []
    for lr in bundle["ledger_rows"]:
        f = lr["fee"]
        ft = f.fee_structure.fee_type
        rows.append(
            {
                "fee_type_name": ft.name if ft else "—",
                "original": lr["original"],
                "discount": lr["discount"],
                "final_due": lr["final_due"],
                "paid": lr["paid"],
                "balance": lr["balance"],
                "status": lr["status"],
            }
        )

    st_user = student.user
    name = st_user.get_full_name() or st_user.username

    stmt_date = timezone.now().date()
    if academic_year:
        inv_no = f"INV-{academic_year.pk}-{student.pk}"
    else:
        inv_no = f"INV-ALL-{student.pk}"

    return {
        "is_pdf": False,
        "school": brand,
        "document": {"number": inv_no, "date": stmt_date},
        "doc_subtitle": (
            f"Academic year {academic_year.name}" if academic_year else "All periods"
        ),
        "invoice_title": "INVOICE",
        "subtitle": academic_year.name if academic_year else "All periods",
        "student": student,
        "student_name": name,
        "student_class": student.classroom.name if student.classroom_id else "—",
        "student_section": student.section.name if student.section_id else "—",
        "admission_number": (student.admission_number or "").strip() or "—",
        "academic_year": academic_year,
        "ledger_rows": rows,
        "total_original": bundle["total_original"],
        "total_discount": bundle["total_discount"],
        "total_final": bundle["total_final"],
        "total_paid": bundle["total_paid_sum"],
        "total_pending": bundle["total_balance_sum"],
    }
