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
from apps.customers.models import Domain, School, SubscriptionPlan
from apps.super_admin.models import Plan, PlanName

from .models import SchoolEnrollmentRequest

from django.core.exceptions import ValidationError as DjangoValidationError

from .tenant_provisioning import (
    generate_unique_base36_school_code,
    schema_name_for_school_code,
)

logger = logging.getLogger(__name__)


class SelfServiceEnrollmentError(Exception):
    """User-safe message for form non-field errors."""


def _compose_address(data: dict[str, Any]) -> str:
    lines = []
    soc = (data.get("society_name") or "").strip()
    if soc:
        lines.append(f"Society / registered name: {soc}")
    if (data.get("address") or "").strip():
        lines.append(data["address"].strip())
    loc = ", ".join(
        x
        for x in [data.get("city"), data.get("state"), data.get("pincode")]
        if x and str(x).strip()
    )
    if loc:
        lines.append(loc)
    lm = (data.get("landmark") or "").strip()
    if lm:
        lines.append(f"Landmark: {lm}")
    dist = (data.get("district") or "").strip()
    if dist:
        lines.append(f"District: {dist}")
    lat, lng = data.get("latitude"), data.get("longitude")
    if lat is not None and lng is not None:
        lines.append(f"Coordinates: {lat}, {lng}")
    maps = (data.get("maps_url") or "").strip()
    if maps:
        lines.append(f"Maps: {maps}")
    if (data.get("notes") or "").strip():
        lines.append(data["notes"].strip())
    return "\n\n".join(lines).strip()


def seed_tenant_bootstrap(school: School) -> None:
    """
    Seed tenant-scoped defaults that every school can later change in Master Dropdown Settings.

    Ensures at least one academic year exists so class/student pickers are usable immediately.
    """
    try:
        from apps.school_data.master_data_defaults import (
            ensure_default_academic_years,
            ensure_master_data_defaults,
        )

        ensure_master_data_defaults(school)
        ensure_default_academic_years(school)
    except Exception:
        logger.exception("Master data defaults seed failed for schema %s", school.schema_name)


def _create_audit_enrollment_row(
    *,
    cleaned_data: dict[str, Any],
    school: School,
    acting_user: User,
) -> None:
    """Persist enrollment request for super-admin audit (no password stored)."""
    SchoolEnrollmentRequest.objects.create(
        institution_name=cleaned_data["institution_name"].strip(),
        society_name=(cleaned_data.get("society_name") or "").strip()[:255],
        institution_code=(school.code or "")[:100],
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
        website=(cleaned_data.get("website") or "").strip()[:500],
        affiliation_board=(cleaned_data.get("affiliation_board") or "").strip()[:120],
        school_type=(cleaned_data.get("school_type") or "").strip()[:32],
        established_year=cleaned_data.get("established_year"),
        school_motto=(cleaned_data.get("school_motto") or "").strip()[:300],
        affiliation_number=(cleaned_data.get("affiliation_number") or "").strip()[:120],
        landmark=(cleaned_data.get("landmark") or "").strip()[:255],
        district=(cleaned_data.get("district") or "").strip()[:120],
        latitude=cleaned_data.get("latitude"),
        longitude=cleaned_data.get("longitude"),
        maps_url=(cleaned_data.get("maps_url") or "").strip()[:500],
        alternate_contact_name=(cleaned_data.get("alternate_contact_name") or "").strip()[:255],
        alternate_contact_phone=(cleaned_data.get("alternate_contact_phone") or "").strip()[:40],
        admin_designation=(cleaned_data.get("admin_designation") or "").strip()[:64],
        admin_profile_photo=cleaned_data.get("admin_profile_photo"),
        instruction_medium=(cleaned_data.get("instruction_medium") or "").strip()[:32],
        classes_offered="",
        streams_offered=(cleaned_data.get("streams_offered") or "").strip()[:200],
        sections_per_class_notes=(cleaned_data.get("sections_per_class_notes") or "").strip()[:200],
        curriculum_type=(cleaned_data.get("curriculum_type") or "").strip()[:32],
        total_classrooms=cleaned_data.get("total_classrooms"),
        lab_physics=cleaned_data.get("lab_physics"),
        lab_chemistry=cleaned_data.get("lab_chemistry"),
        lab_computer=cleaned_data.get("lab_computer"),
        has_library=cleaned_data.get("has_library"),
        has_playground=cleaned_data.get("has_playground"),
        has_transport=cleaned_data.get("has_transport"),
        total_student_capacity=cleaned_data.get("total_student_capacity"),
        current_student_strength=cleaned_data.get("current_student_strength"),
        non_teaching_staff_count=cleaned_data.get("non_teaching_staff_count"),
        uses_erp=cleaned_data.get("uses_erp"),
        current_erp_name=(cleaned_data.get("current_erp_name") or "").strip()[:120],
        require_data_migration=cleaned_data.get("require_data_migration"),
        preferred_ui_language=(cleaned_data.get("preferred_ui_language") or "").strip()[:32],
        expected_start_date=cleaned_data.get("expected_start_date"),
        detailed_requirements=(cleaned_data.get("detailed_requirements") or "").strip(),
        school_logo=cleaned_data.get("school_logo"),
        registration_certificate=cleaned_data.get("registration_certificate"),
        address_proof=cleaned_data.get("address_proof"),
        other_documents=cleaned_data.get("other_documents"),
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

    trial_bp = SubscriptionPlan.objects.filter(name__iexact="trial", is_active=True).first()

    addr = _compose_address(cleaned_data)

    intended = (cleaned_data.get("intended_plan") or "premium").strip().lower()
    if intended == "monthly":
        intended = "basic"
    legacy = {
        "core": "basic",
        "advance": "pro",
        "standard": "pro",
        "enterprise": "premium",
        "yearly": "basic",
        "trial": "premium",
    }
    if intended in legacy:
        intended = legacy[intended]
    if intended not in {"basic", "pro", "premium"}:
        intended = "premium"

    tier_plan = Plan.objects.filter(name=intended, is_active=True).first()
    if not tier_plan:
        tier_plan = (
            Plan.objects.filter(name=PlanName.PREMIUM, is_active=True).first()
            or Plan.objects.filter(name=PlanName.PRO, is_active=True).first()
            or Plan.objects.filter(name=PlanName.BASIC, is_active=True).first()
        )

    trial_days = 14
    if trial_bp and getattr(trial_bp, "duration_days", None):
        trial_days = int(trial_bp.duration_days) or 14

    school: School | None = None
    for attempt in range(5):
        try:
            code = generate_unique_base36_school_code()
        except DjangoValidationError as exc:
            msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            raise SelfServiceEnrollmentError(msg) from None

        schema_name = schema_name_for_school_code(code)
        if School.objects.filter(code=code).exists() or School.objects.filter(schema_name=schema_name).exists():
            continue

        candidate = School(
            name=cleaned_data["institution_name"].strip(),
            code=code,
            schema_name=schema_name,
            contact_email=cleaned_data["email"].strip(),
            phone=(cleaned_data.get("phone") or "")[:20],
            address=addr,
            contact_person=cleaned_data["contact_name"].strip(),
            website=(cleaned_data.get("website") or "").strip()[:500],
            board_affiliation=(cleaned_data.get("affiliation_board") or "").strip()[:120],
            school_status=School.SchoolStatus.TRIAL,
            plan=tier_plan,
            billing_plan=trial_bp,
            trial_end_date=date.today() + timedelta(days=trial_days),
        )
        try:
            candidate.save()
            school = candidate
            break
        except IntegrityError:
            logger.warning(
                "Self-service enrollment: IntegrityError on school.save (attempt %s), retrying new code",
                attempt + 1,
            )
            continue
        except Exception as exc:
            logger.exception("School.save() failed during self-service enrollment")
            msg = "We could not finish creating your school workspace. Please try again or contact support."
            if settings.DEBUG:
                detail = str(exc).strip() or exc.__class__.__name__
                msg = f"{msg} (debug: {detail})"
            raise SelfServiceEnrollmentError(msg) from None

    if school is None:
        raise SelfServiceEnrollmentError(
            "Could not assign a unique school code. Please try again or contact support."
        )

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
