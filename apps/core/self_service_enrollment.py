"""
Self-service /enroll/: create School (tenant), admin User, audit row. Tenant academic data stays empty until the school configures it.
Runs on public schema; School.save() creates PostgreSQL schema + migrations (django-tenants).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.accounts.models import User
from apps.customers.models import Domain, Plan as CustomerPlan, School, SubscriptionPlan

from .models import SchoolEnrollmentRequest
from apps.customers.subscription import PLAN_FEATURES

from django.core.exceptions import ValidationError as DjangoValidationError

from .tenant_provisioning import (
    generate_unique_school_code_from_name,
    schema_name_for_school_code,
    validate_school_code_format,
)

logger = logging.getLogger(__name__)


class SelfServiceEnrollmentError(Exception):
    """User-safe message for form non-field errors."""


def _resolve_enrollment_school_code(cleaned_data: dict[str, Any]) -> str:
    """ABC123 from form or auto-generated from school name (unique)."""
    raw = (cleaned_data.get("institution_code") or "").strip()
    if not raw:
        return generate_unique_school_code_from_name(cleaned_data["institution_name"])
    return validate_school_code_format(raw)


def _compose_address(data: dict[str, Any]) -> str:
    lines = []
    if (data.get("address") or "").strip():
        lines.append(data["address"].strip())
    loc = ", ".join(
        x
        for x in [data.get("city"), data.get("state"), data.get("pincode")]
        if x and str(x).strip()
    )
    if loc:
        lines.append(loc)
    if (data.get("notes") or "").strip():
        lines.append(data["notes"].strip())
    return "\n\n".join(lines).strip()


def seed_tenant_bootstrap(school: School) -> None:
    """
    New tenants start empty: no academic years, sections, classes, subjects, fees, or routes.

    Isolation is per PostgreSQL schema (django-tenants): school_data models intentionally omit a
    school_id FK because each school only exists in its own schema. The public School row + Domain
    + admin User are created separately by the enrollment flow.
    """
    del school  # provisioning only ensures schema exists via School.save(); no seed rows.


def _create_audit_enrollment_row(
    *,
    cleaned_data: dict[str, Any],
    school: School,
    acting_user: User,
) -> None:
    """Persist enrollment request for super-admin audit (no password stored)."""
    SchoolEnrollmentRequest.objects.create(
        institution_name=cleaned_data["institution_name"].strip(),
        institution_code=(cleaned_data.get("institution_code") or "").strip(),
        contact_name=cleaned_data["contact_name"].strip(),
        email=cleaned_data["email"].strip(),
        phone=(cleaned_data.get("phone") or "")[:30],
        address=(cleaned_data.get("address") or "").strip(),
        city=(cleaned_data.get("city") or "").strip(),
        state=(cleaned_data.get("state") or "").strip(),
        pincode=(cleaned_data.get("pincode") or "").strip(),
        student_count=cleaned_data.get("student_count"),
        teacher_count=cleaned_data.get("teacher_count"),
        branch_count=cleaned_data.get("branch_count"),
        preferred_username=cleaned_data["preferred_username"].strip(),
        pending_password_hash="",
        intended_plan=(cleaned_data.get("intended_plan") or "trial").strip().lower(),
        notes=(cleaned_data.get("notes") or "")[:250],
        status=SchoolEnrollmentRequest.Status.PROVISIONED,
        school=school,
        provisioned_schema_name=school.schema_name,
        reviewed_at=timezone.now(),
        reviewed_by=acting_user,
    )


def provision_school_and_admin_user(cleaned_data: dict[str, Any]) -> tuple[School, User]:
    """
    Create tenant school, run tenant migrations via TenantMixin, create admin user on public schema.
    Tenant academic tables stay empty until the school configures them.
    Caller must use @transaction.non_atomic_requests on the view (School.save runs migrate_schemas).
    """
    connection.set_schema_to_public()
    UserModel = get_user_model()

    username = cleaned_data["preferred_username"].strip()
    if UserModel.objects.filter(username=username).exists():
        raise SelfServiceEnrollmentError("That username is already taken. Please choose another.")

    try:
        code = _resolve_enrollment_school_code(cleaned_data)
    except DjangoValidationError as exc:
        msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
        raise SelfServiceEnrollmentError(msg) from None

    if School.objects.filter(code=code).exists():
        raise SelfServiceEnrollmentError(
            "School code already exists. Please choose another code."
        )

    schema_name = schema_name_for_school_code(code)

    trial_sp = SubscriptionPlan.objects.filter(name__iexact="trial", is_active=True).first()
    starter = CustomerPlan.objects.filter(name="Starter").first()
    standard_tier = CustomerPlan.objects.filter(name="Standard").first()
    enterprise = CustomerPlan.objects.filter(name="Enterprise").first()

    addr = _compose_address(cleaned_data)

    intended = (cleaned_data.get("intended_plan") or "trial").strip().lower()
    if intended == "monthly":
        intended = "basic"
    if intended not in {"trial", "basic", "standard", "enterprise", "yearly"}:
        intended = "trial"

    if intended == "enterprise":
        saas_tier = enterprise or standard_tier or starter
    elif intended == "standard":
        saas_tier = standard_tier or enterprise or starter
    elif intended in ("basic", "yearly"):
        saas_tier = starter
    else:
        saas_tier = starter
    if not saas_tier:
        saas_tier = starter or standard_tier or enterprise

    trial_days = 14
    if trial_sp and getattr(trial_sp, "duration_days", None):
        trial_days = int(trial_sp.duration_days) or 14

    # Full feature access during trial (conversion-friendly); billing still trial.
    pro_codes = list(dict.fromkeys(PLAN_FEATURES.get("pro", []) + PLAN_FEATURES.get("basic", [])))
    if "teachers" not in pro_codes:
        pro_codes.append("teachers")

    school = School(
        name=cleaned_data["institution_name"].strip(),
        code=code,
        schema_name=schema_name,
        contact_email=cleaned_data["email"].strip(),
        phone=(cleaned_data.get("phone") or "")[:20],
        address=addr,
        contact_person=cleaned_data["contact_name"].strip(),
        school_status=School.SchoolStatus.TRIAL,
        plan=trial_sp,
        saas_plan=saas_tier,
        trial_end_date=date.today() + timedelta(days=trial_days),
        enabled_features_override=pro_codes if pro_codes else None,
    )

    try:
        school.save()
    except Exception as exc:
        logger.exception("School.save() failed during self-service enrollment")
        msg = "We could not finish creating your school workspace. Please try again or contact support."
        if settings.DEBUG:
            detail = str(exc).strip() or exc.__class__.__name__
            msg = f"{msg} (debug: {detail})"
        raise SelfServiceEnrollmentError(msg) from None

    if not Domain.objects.filter(tenant=school).exists():
        Domain.objects.create(
            domain=f"{school.schema_name}.localhost",
            tenant=school,
            is_primary=True,
        )

    try:
        seed_tenant_bootstrap(school)
    except Exception:
        logger.exception("Tenant seed failed for schema %s", school.schema_name)

    names = (cleaned_data.get("contact_name") or "").strip().split()
    first = names[0] if names else ""
    last = " ".join(names[1:])[:150] if len(names) > 1 else ""

    user = UserModel(
        username=username,
        email=cleaned_data["email"].strip(),
        role=User.Roles.ADMIN,
        school=school,
        is_active=True,
        is_staff=False,
        first_name=first[:150],
        last_name=last,
        phone_number=(cleaned_data.get("phone") or "")[:20],
    )
    user.set_password(cleaned_data["password2"])
    try:
        user.save()
    except IntegrityError:
        logger.exception("User save failed (integrity) during self-service enrollment")
        raise SelfServiceEnrollmentError(
            "That username or email conflicts with an existing account. Try a different username."
        ) from None

    try:
        _create_audit_enrollment_row(cleaned_data=cleaned_data, school=school, acting_user=user)
    except Exception:
        logger.exception("Audit enrollment row failed (non-fatal)")

    return school, user
