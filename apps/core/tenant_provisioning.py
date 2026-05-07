"""
Create School tenants (PostgreSQL schema + django-tenants migrations) from public schema.
Used by super-admin UI after a public enrollment signup.

School code formats:
- Legacy / manual: exactly 6 characters — 3 letters + 3 digits (e.g. NHS123); schema = code.lower().
- Self-service enroll: 6-character Base36 (0-9, A-Z), uppercase, unique; schema = code.lower(), or
  ``s`` + code.lower() when the code starts with a digit (valid PostgreSQL identifier).
"""
from __future__ import annotations

import logging
import random
import re
import string
from datetime import date, timedelta

logger = logging.getLogger(__name__)

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from apps.customers.models import Domain, Plan as CustomerPlan, School, SubscriptionPlan

# Mandatory: 3 uppercase letters + 3 digits (e.g. NHS123, DPS456)
SCHOOL_CODE_PATTERN = re.compile(r"^[A-Z]{3}[0-9]{3}$")
# Self-service enrollment: 6-char Base36 (uppercase alnum)
BASE36_ALPHABET = string.digits + string.ascii_uppercase
SCHOOL_CODE_BASE36_PATTERN = re.compile(r"^[0-9A-Z]{6}$")


def validate_school_code_format(code: str) -> str:
    """
    Return normalized school code: strip whitespace, uppercase letters, then validate
    ABC123 (3 letters + 3 digits). Lowercase input (e.g. nhs123) is accepted.
    """
    c = (code or "").strip().upper()
    if not SCHOOL_CODE_PATTERN.fullmatch(c):
        raise ValidationError(
            "School code must be in format ABC123 (3 letters + 3 numbers)."
        )
    return c


def generate_unique_school_code_from_name(name: str) -> str:
    """
    Auto-generate ABC123: first 3 letters from institution name + random 3 digits.
    Pads with X if fewer than 3 letters. Uniqueness enforced against School.code.
    """
    letters = re.sub(r"[^A-Za-z]", "", name or "").upper()
    prefix = (letters[:3].ljust(3, "X"))[:3]
    for _ in range(2000):
        suffix = f"{random.randint(0, 999):03d}"
        candidate = f"{prefix}{suffix}"
        if not School.objects.filter(code=candidate).exists():
            return candidate
    raise ValidationError(
        "Could not generate a unique school code. Please enter a school code manually."
    )


def schema_name_for_school_code(code: str) -> str:
    """
    PostgreSQL schema name for a school ``code``.

    - ABC123 -> ``nhs123`` (lowercase legacy format).
    - Base36 6-char (e.g. K9X2A7) -> lowercase; if it starts with a digit, prefix ``s`` so the
      identifier is valid for PostgreSQL (identifiers must not start with a digit).
    """
    c = (code or "").strip().upper()
    if SCHOOL_CODE_PATTERN.fullmatch(c):
        return c.lower()
    if SCHOOL_CODE_BASE36_PATTERN.fullmatch(c):
        lo = c.lower()
        if lo[0].isdigit():
            return f"s{lo}"
        return lo
    validated = validate_school_code_format(code)
    return validated.lower()


def schema_slug_from_school_code(code: str) -> str:
    """Backward-compatible alias: same as schema_name_for_school_code for valid ABC123 codes."""
    return schema_name_for_school_code(code)


def allocate_unique_schema_name(seed: str) -> str:
    """
    Legacy helper for non-standard seeds (underscores, long codes). Prefer schema_name_for_school_code
    for new enrollments. Ensures PostgreSQL identifier rules and uniqueness of schema_name.
    """
    base = re.sub(r"[^a-zA-Z0-9_]", "", (seed or "").lower())[:50] or "school"
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


def generate_school_code_from_name(name: str) -> str:
    """Deprecated name: use generate_unique_school_code_from_name."""
    return generate_unique_school_code_from_name(name)


def generate_base36_school_code(length: int = 6) -> str:
    """Random Base36 uppercase string (digits + A-Z), fixed length."""
    return "".join(random.choices(BASE36_ALPHABET, k=length))


def generate_unique_base36_school_code(length: int = 6) -> str:
    """
    Unique 6-character Base36 school code + resolvable unique schema_name.
    Retries on collision (exist check); caller may still wrap ``School.save()`` in IntegrityError retries.
    """
    for _ in range(256):
        candidate = generate_base36_school_code(length)
        try:
            schema = schema_name_for_school_code(candidate)
        except ValidationError:
            continue
        if School.objects.filter(code=candidate).exists():
            continue
        if School.objects.filter(schema_name=schema).exists():
            continue
        return candidate
    raise ValidationError(
        "Could not allocate a unique school code. Please try again in a moment."
    )


def provision_school_from_enrollment(
    *,
    institution_name: str,
    contact_email: str,
    phone: str = "",
    address_notes: str = "",
    subscription_plan: SubscriptionPlan | None = None,
    saas_plan: CustomerPlan | None = None,
    school_code: str | None = None,
) -> School:
    """
    Create School on the public schema: new PostgreSQL schema + migrate_schemas for that tenant.
    Caller must be in public schema context (e.g. superadmin, no tenant middleware switch).

    school_code: optional ABC123. If omitted, a unique code is generated from institution_name.
    """
    connection.set_schema_to_public()

    if school_code and str(school_code).strip():
        code = validate_school_code_format(school_code)
    else:
        code = generate_unique_school_code_from_name(institution_name)

    if School.objects.filter(code=code).exists():
        raise ValidationError(
            "School code already exists. Please choose another code."
        )

    schema_name = schema_name_for_school_code(code)
    if School.objects.filter(schema_name=schema_name).exists():
        schema_name = allocate_unique_schema_name(schema_name)

    school = School(
        name=institution_name.strip(),
        code=code,
        schema_name=schema_name,
        contact_email=contact_email.strip(),
        phone=phone.strip()[:20],
        address=address_notes.strip(),
    )
    # Super Admin SaaS Plan (v2)
    try:
        from apps.super_admin.models import Plan as SaaSPlan, PlanName

        school.plan = SaaSPlan.objects.filter(name=PlanName.BASIC, is_active=True).first()
    except Exception:
        school.plan = None

    if subscription_plan:
        school.billing_plan = subscription_plan
        if (subscription_plan.name or "").lower() == "trial":
            school.trial_end_date = date.today() + timedelta(days=subscription_plan.duration_days)

    school.save()

    domain_host = f"{schema_name}.localhost"
    if not Domain.objects.filter(tenant=school).exists():
        Domain.objects.create(domain=domain_host, tenant=school, is_primary=True)

    try:
        from apps.school_data.master_data_defaults import ensure_master_data_defaults

        ensure_master_data_defaults(school)
    except Exception:
        logger.exception("Master data defaults seed failed after provisioning schema %s", schema_name)

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
