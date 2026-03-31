"""
Create School tenants (PostgreSQL schema + django-tenants migrations) from public schema.
Used by super-admin UI after a public enrollment signup.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from django.db import connection, transaction
from django.utils import timezone

from apps.customers.models import Domain, Plan as CustomerPlan, School, SubscriptionPlan


def generate_school_code_from_name(name: str) -> str:
    """Unique school code from institution name, e.g. 'Green Valley' -> 'GV001'."""
    parts = re.sub(r"[^a-zA-Z0-9\s]", "", name).split()
    initials = "".join(p[:1].upper() for p in parts[:3]) or "SCH"
    count = School.objects.filter(code__startswith=initials).count() + 1
    return f"{initials}{count:03d}"


def allocate_unique_schema_name(seed: str) -> str:
    """
    PostgreSQL schema name for django-tenants (63 chars max, not starting with pg_).
    """
    base = re.sub(r"[^a-zA-Z0-9_]", "", seed.lower())[:50] or "school"
    if base.startswith("pg_"):
        base = "t_" + base[3:]
    base = base[:63]
    candidate = base
    n = 0
    while School.objects.filter(schema_name=candidate).exists():
        n += 1
        suffix = f"_{n}"
        candidate = (base[: 63 - len(suffix)] + suffix) if len(suffix) < 63 else f"t{n}"
    return candidate


def provision_school_from_enrollment(
    *,
    institution_name: str,
    contact_email: str,
    phone: str = "",
    address_notes: str = "",
    subscription_plan: SubscriptionPlan | None = None,
) -> School:
    """
    Create School on the public schema: new PostgreSQL schema + migrate_schemas for that tenant.
    Caller must be in public schema context (e.g. superadmin, no tenant middleware switch).
    """
    connection.set_schema_to_public()

    code = generate_school_code_from_name(institution_name)
    schema_name = allocate_unique_schema_name(code)

    school = School(
        name=institution_name.strip(),
        code=code,
        schema_name=schema_name,
        contact_email=contact_email.strip(),
        phone=phone.strip()[:20],
        address=address_notes.strip(),
    )
    if saas_plan:
        school.saas_plan = saas_plan
    elif subscription_plan:
        nm = (subscription_plan.name or "").lower()
        if nm == "pro":
            school.saas_plan = CustomerPlan.objects.filter(name="Enterprise").first()
        else:
            school.saas_plan = CustomerPlan.objects.filter(name="Starter").first()
    if subscription_plan:
        school.plan = subscription_plan
        if (subscription_plan.name or "").lower() == "trial":
            school.trial_end_date = date.today() + timedelta(days=subscription_plan.duration_days)

    school.save()

    domain_host = f"{schema_name}.localhost"
    if not Domain.objects.filter(tenant=school).exists():
        Domain.objects.create(domain=domain_host, tenant=school, is_primary=True)

    return school


@transaction.atomic
def mark_enrollment_provisioned(enrollment, school: School, user) -> None:
    enrollment.status = enrollment.Status.PROVISIONED
    enrollment.school = school
    enrollment.provisioned_schema_name = school.schema_name
    enrollment.reviewed_at = timezone.now()
    enrollment.reviewed_by = user
    enrollment.decline_reason = ""
    enrollment.save(
        update_fields=[
            "status",
            "school",
            "provisioned_schema_name",
            "reviewed_at",
            "reviewed_by",
            "decline_reason",
        ]
    )


@transaction.atomic
def mark_enrollment_declined(enrollment, user, reason: str = "") -> None:
    enrollment.status = enrollment.Status.DECLINED
    enrollment.reviewed_at = timezone.now()
    enrollment.reviewed_by = user
    enrollment.decline_reason = reason.strip()
    enrollment.save(
        update_fields=["status", "reviewed_at", "reviewed_by", "decline_reason"]
    )
