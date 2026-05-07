"""
Strict, production-oriented plan feature resolution (public schema).

* ``customers.Feature.code`` and ``super_admin.Feature.code`` are the **feature_key**.
* Tier bundles (Basic / Pro / Premium) define defaults when a plan row has no M2M rows yet.
* ``School.get_enabled_feature_codes()`` materializes legacy route keys (``attendance``, ``sms``)
  from canonical subscription/plan codes so existing URLs and gates keep working.
"""
from __future__ import annotations

import logging
from typing import Iterable

from django_tenants.utils import get_public_schema_name, schema_context

from apps.core.subscription_access import normalize_feature_key

logger = logging.getLogger(__name__)

MESSAGING_ALIASES = frozenset({"messaging", "platform_messaging"})

# --- Product tier definitions (source of truth when DB plan M2M is empty) ---

BASIC_FEATURES: frozenset[str] = frozenset(
    {
        "academic_year",
        "classes",
        "sections",
        "students",
        "subjects",
        "teachers",
    }
)

PRO_FEATURES: frozenset[str] = frozenset(
    BASIC_FEATURES
    | {
        "exams",
        "homework",
        "calendar",
        "reports",
        "attendance_student",
        "attendance_teacher",
        "timetable",
    }
)

# Premium: product list + operational modules still used by the ERP shell.
PREMIUM_EXTRA: frozenset[str] = frozenset(
    {
        "broadcast",
        "messaging",
        "notifications",
        "platform_messaging",
        "fees",
        "analytics",
    }
)

PREMIUM_OPS: frozenset[str] = frozenset(
    {
        "library",
        "hostel",
        "transport",
        "payroll",
        "online_admission",
        "custom_branding",
        "topper_list",
        "sms",
        "ai_reports",
        "ai_marksheet_summaries",
        "inventory",
        "online_results",
        "api_access",
        "priority_support",
    }
)

PREMIUM_FEATURES: frozenset[str] = frozenset(PRO_FEATURES | PREMIUM_EXTRA | PREMIUM_OPS)

_TIER_NAME_DEFAULTS: dict[str, frozenset[str]] = {
    "basic": BASIC_FEATURES,
    "pro": PRO_FEATURES,
    "premium": PREMIUM_FEATURES,
}


def get_allowed_features(school_id: int) -> list[str]:
    """
    Return sorted canonical feature keys for the school's **base** plan (subscription first,
    else super_admin plan, else Basic tier defaults). Does **not** include add-ons; use
    ``materialize_feature_set`` / ``build_enabled_materialized`` for enforcement.
    """
    from apps.customers.models import School

    with schema_context(get_public_schema_name()):
        school = School.objects.filter(pk=int(school_id)).first()
        if not school:
            return []
        base = resolve_base_canonical_codes(school)
        return sorted(base)


def resolve_base_canonical_codes(school) -> set[str]:
    """Canonical codes from subscription, else super_admin plan, else tier defaults."""
    sub = school._feature_codes_from_customer_subscription()
    if sub is not None:
        return {normalize_feature_key(str(c)) for c in sub if str(c).strip()}

    if getattr(school, "plan_id", None):
        try:
            from apps.super_admin.models import Plan as SaPlan

            p = SaPlan.objects.filter(pk=school.plan_id).prefetch_related("features").first()
            if not p:
                return set(BASIC_FEATURES)
            raw = {normalize_feature_key(str(c)) for c in p.features.values_list("code", flat=True)}
            if raw:
                return raw
            name = (p.name or "").strip().lower()
            return set(_TIER_NAME_DEFAULTS.get(name, BASIC_FEATURES))
        except Exception:
            logger.exception("plan_features: could not load super_admin plan")
            return set(BASIC_FEATURES)

    return set(BASIC_FEATURES)


def materialize_feature_set(canonical: Iterable[str]) -> frozenset[str]:
    """
    Expand canonical plan codes with legacy **route / gate** keys used across the codebase.
    """
    out = {normalize_feature_key(str(c)) for c in canonical if c is not None and str(c).strip()}

    if out & {"attendance_student", "attendance_teacher", "attendance"}:
        out.add("attendance")

    if out & MESSAGING_ALIASES:
        out |= MESSAGING_ALIASES

    if "notifications" in out:
        out.add("sms")

    if "analytics" in out:
        out.add("ai_reports")

    if "broadcast" in out:
        out.add("sms")

    return frozenset(out)


def feature_granted(materialized: frozenset[str], feature_key: str) -> bool:
    """Whether a materialized entitlement set allows ``feature_key`` (handles composite gates)."""
    k = normalize_feature_key(feature_key or "")
    if not k:
        return False
    if k in materialized:
        return True
    if k == "attendance":
        return bool(materialized & {"attendance_student", "attendance_teacher", "attendance"})
    if k in MESSAGING_ALIASES:
        return bool(materialized & MESSAGING_ALIASES)
    if k == "sms":
        return "sms" in materialized or "notifications" in materialized
    if k in ("reports", "topper_list"):
        return bool(materialized & {"reports", "analytics", "ai_reports"})
    if k == "ai_reports":
        return "ai_reports" in materialized or "analytics" in materialized
    if k == "ai_marksheet_summaries":
        return "ai_marksheet_summaries" in materialized or "analytics" in materialized
    return False


def build_enabled_materialized(school) -> frozenset[str]:
    """Base canonical + enabled add-ons, then materialized for ``request.school_features``."""
    base = resolve_base_canonical_codes(school)
    try:
        extra = {
            normalize_feature_key(str(c))
            for c in school.feature_addons.filter(is_enabled=True).values_list("feature__code", flat=True)
        }
    except Exception:
        extra = set()
    return materialize_feature_set(base | extra)


def has_feature_access(school_id, feature_key: str) -> bool:
    """Public API: numeric school PK or school ``code`` string (``User.school_id``)."""
    from apps.customers.models import School

    with schema_context(get_public_schema_name()):
        school = None
        if school_id is not None:
            sid = str(school_id).strip()
            if sid.isdigit():
                school = School.objects.filter(pk=int(sid)).first()
            if school is None and sid:
                school = School.objects.filter(code=sid).first()
        if not school:
            return False
        mat = build_enabled_materialized(school)
        return feature_granted(mat, feature_key)


def has_feature_for_school(school, feature_key: str) -> bool:
    if not school:
        return False
    return feature_granted(build_enabled_materialized(school), feature_key)


def seed_super_admin_tier_features() -> None:
    """
    Ensure ``super_admin.Feature`` rows exist and Basic / Pro / Premium plans match product tiers.

    Safe to call on every Control Center load (idempotent).
    """
    from apps.super_admin.models import Feature, FeatureCategory, Plan, PlanName

    rows: list[tuple[str, str, str]] = [
        ("Academic year", "academic_year", FeatureCategory.ACADEMIC),
        ("Classes", "classes", FeatureCategory.ACADEMIC),
        ("Sections", "sections", FeatureCategory.ACADEMIC),
        ("Students", "students", FeatureCategory.ACADEMIC),
        ("Subjects", "subjects", FeatureCategory.ACADEMIC),
        ("Teachers", "teachers", FeatureCategory.ACADEMIC),
        ("Exams", "exams", FeatureCategory.EXAMS),
        ("Homework", "homework", FeatureCategory.EXAMS),
        ("Calendar", "calendar", FeatureCategory.OPERATIONS),
        ("Reports", "reports", FeatureCategory.EXAMS),
        ("Attendance (students)", "attendance_student", FeatureCategory.OPERATIONS),
        ("Attendance (teachers)", "attendance_teacher", FeatureCategory.OPERATIONS),
        ("Timetable", "timetable", FeatureCategory.OPERATIONS),
        ("Broadcast", "broadcast", FeatureCategory.COMMUNICATION),
        ("Messaging", "messaging", FeatureCategory.COMMUNICATION),
        ("Notifications", "notifications", FeatureCategory.COMMUNICATION),
        ("Platform messaging", "platform_messaging", FeatureCategory.COMMUNICATION),
        ("Fees", "fees", FeatureCategory.FINANCE),
        ("Analytics", "analytics", FeatureCategory.EXAMS),
        ("Library", "library", FeatureCategory.OPERATIONS),
        ("Hostel", "hostel", FeatureCategory.OPERATIONS),
        ("Transport", "transport", FeatureCategory.OPERATIONS),
        ("Payroll", "payroll", FeatureCategory.FINANCE),
        ("Online admission", "online_admission", FeatureCategory.OPERATIONS),
        ("Custom branding", "custom_branding", FeatureCategory.OPERATIONS),
        ("Topper list", "topper_list", FeatureCategory.EXAMS),
        ("SMS", "sms", FeatureCategory.COMMUNICATION),
        ("AI reports", "ai_reports", FeatureCategory.EXAMS),
        ("Inventory", "inventory", FeatureCategory.OPERATIONS),
        ("Online results", "online_results", FeatureCategory.EXAMS),
        ("API access", "api_access", FeatureCategory.OPERATIONS),
        ("Priority support", "priority_support", FeatureCategory.OPERATIONS),
        ("AI marksheet summaries", "ai_marksheet_summaries", FeatureCategory.EXAMS),
        # Legacy single attendance (maps via materialize when either student/teacher present)
        ("Attendance", "attendance", FeatureCategory.OPERATIONS),
    ]
    by_code: dict[str, Feature] = {}
    for name, code, cat in rows:
        f, _ = Feature.objects.update_or_create(code=code, defaults={"name": name, "category": cat})
        by_code[code] = f

    def attach(plan_name: str, codes: frozenset[str]) -> None:
        p = Plan.objects.filter(name=plan_name).first()
        if not p:
            return
        objs = [by_code[c] for c in codes if c in by_code]
        p.features.set(objs)

    attach(PlanName.BASIC, BASIC_FEATURES)
    attach(PlanName.PRO, PRO_FEATURES)
    attach(PlanName.PREMIUM, PREMIUM_FEATURES)


def seed_customer_tier_plans() -> None:
    """Sync ``customers.Feature`` / ``customers.Plan`` (Basic / Pro / Premium) with the same tier bundles."""
    from apps.customers.models import Feature, Plan

    specs: list[tuple[str, str]] = [
        ("Academic year", "academic_year"),
        ("Classes", "classes"),
        ("Sections", "sections"),
        ("Students", "students"),
        ("Subjects", "subjects"),
        ("Teachers", "teachers"),
        ("Exams", "exams"),
        ("Homework", "homework"),
        ("Calendar", "calendar"),
        ("Reports", "reports"),
        ("Attendance (students)", "attendance_student"),
        ("Attendance (teachers)", "attendance_teacher"),
        ("Timetable", "timetable"),
        ("Broadcast", "broadcast"),
        ("Messaging", "messaging"),
        ("Notifications", "notifications"),
        ("Platform messaging", "platform_messaging"),
        ("Fees", "fees"),
        ("Analytics", "analytics"),
        ("Library", "library"),
        ("Hostel", "hostel"),
        ("Transport", "transport"),
        ("Payroll", "payroll"),
        ("Online admission", "online_admission"),
        ("Custom branding", "custom_branding"),
        ("Topper list", "topper_list"),
        ("SMS", "sms"),
        ("AI reports", "ai_reports"),
        ("Inventory", "inventory"),
        ("Online results", "online_results"),
        ("API access", "api_access"),
        ("Priority support", "priority_support"),
        ("AI marksheet summaries", "ai_marksheet_summaries"),
    ]
    by_code: dict[str, Feature] = {}
    for name, code in specs:
        f, _ = Feature.objects.update_or_create(
            code=code,
            defaults={"name": name, "description": ""},
        )
        by_code[code] = f

    def attach_customer(plan_name: str, codes: frozenset[str]) -> None:
        p = Plan.objects.filter(name=plan_name, is_active=True).first()
        if not p:
            p, _ = Plan.objects.get_or_create(
                name=plan_name,
                defaults={
                    "price_per_student": 0,
                    "billing_cycle": Plan.BillingCycle.MONTHLY,
                    "is_active": True,
                    "description": f"Tier {plan_name}",
                },
            )
        objs = [by_code[c] for c in codes if c in by_code]
        p.features.set(objs)

    attach_customer("Basic", BASIC_FEATURES)
    attach_customer("Pro", PRO_FEATURES)
    attach_customer("Premium", PREMIUM_FEATURES)
