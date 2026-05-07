"""
Platform-owner billing snapshot: subscription / trial state per school — not in-school student fees.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import connection, transaction
from django.db.utils import DatabaseError
from django_tenants.utils import tenant_context

from apps.customers.models import School
from apps.school_data.models import ClassRoom, Student, Teacher


def _safe_tenant_footprint(school: School) -> tuple[int, int, int]:
    """
    Teacher, student, class counts for one tenant. Returns (0, 0, 0) if the schema
    has no school_data tables yet (migrations not run) or any DB error.

    Uses transaction.atomic() so a failed query only rolls back a savepoint (when the
    request is already in a transaction) and does not poison the rest of the request.
    """
    try:
        with tenant_context(school):
            with transaction.atomic():
                return (
                    Teacher.objects.count(),
                    Student.objects.count(),
                    ClassRoom.objects.count(),
                )
    except DatabaseError:
        try:
            if not connection.in_atomic_block:
                connection.rollback()
        except Exception:
            pass
        return (0, 0, 0)


@dataclass
class SchoolBillingRow:
    code: str
    name: str
    student_count: int
    plan_tier: str  # Starter / Enterprise / —
    status_key: str
    status_label: str
    trial_ends: date | None
    estimated_mrr: Decimal  # INR / month from SaaS tier × students (paying only)


def _status_for_school(school: School, today: date) -> tuple[str, str]:
    sub = school.billing_plan
    sn = (sub.name if sub else "").lower()
    end = school.trial_end_date

    if sn == "trial":
        if not end:
            return "trial", "Trial (end date not set)"
        if end >= today:
            left = (end - today).days
            return "trial", f"Trial active ({left}d left)"
        return "expired", "Trial ended — needs subscription"

    if sn in ("basic", "pro"):
        return "paying", f"Paying ({sn})"

    return "setup", "No subscription plan"


def _billing_row_for_school(school: School, student_count: int, today: date) -> SchoolBillingRow:
    key, label = _status_for_school(school, today)
    tier = getattr(school.billing_plan, "name", None) or "—"
    mrr = Decimal("0")
    trial_end = school.trial_end_date if (school.billing_plan and (school.billing_plan.name or "").lower() == "trial") else None
    return SchoolBillingRow(
        code=school.code,
        name=school.name,
        student_count=student_count,
        plan_tier=tier,
        status_key=key,
        status_label=label,
        trial_ends=trial_end,
        estimated_mrr=mrr,
    )


def build_school_billing_rows() -> list[SchoolBillingRow]:
    """One tenant context per school (student count only)."""
    today = date.today()
    rows: list[SchoolBillingRow] = []
    schools = (
        School.objects.exclude(schema_name="public")
        .select_related("billing_plan")
        .order_by("name")
    )
    for school in schools:
        _, n, _ = _safe_tenant_footprint(school)
        rows.append(_billing_row_for_school(school, n, today))
    return rows


def build_super_admin_platform_snapshot() -> dict:
    """
    Single pass per tenant: footprint totals + billing rows (for dashboard + financials).
    """
    today = date.today()
    total_teachers = total_students = total_classes = 0
    rows: list[SchoolBillingRow] = []
    schools = list(
        School.objects.exclude(schema_name="public")
        .select_related("saas_plan", "plan")
        .order_by("name")
    )
    for school in schools:
        t, n, c = _safe_tenant_footprint(school)
        total_teachers += t
        total_students += n
        total_classes += c
        rows.append(_billing_row_for_school(school, n, today))
    return {
        "total_schools": len(schools),
        "total_teachers": total_teachers,
        "total_students": total_students,
        "total_classes": total_classes,
        "billing_rows": rows,
    }


def summarize_billing_rows(rows: list[SchoolBillingRow]) -> dict:
    paying = sum(1 for r in rows if r.status_key == "paying")
    trial = sum(1 for r in rows if r.status_key == "trial")
    expired = sum(1 for r in rows if r.status_key == "expired")
    setup = sum(1 for r in rows if r.status_key == "setup")
    unsynced = sum(1 for r in rows if r.status_key == "active_unsynced")
    total_mrr = sum((r.estimated_mrr for r in rows), Decimal("0")).quantize(Decimal("0.01"))
    needs_attention = expired + setup + unsynced
    return {
        "count_paying": paying,
        "count_trial": trial,
        "count_expired": expired,
        "count_setup": setup,
        "count_unsynced": unsynced,
        "needs_attention": needs_attention,
        "total_mrr": total_mrr,
    }
