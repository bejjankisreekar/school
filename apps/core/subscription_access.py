"""
Centralized subscription / plan feature access (public schema).

``customers.Feature.code`` is the canonical **feature_key** (case-insensitive; use lowercase in DB).
``customers.Plan`` + M2M ``features`` define which keys a product tier includes.
``SchoolSubscription`` (``is_current=True``, valid dates, ``is_active``) overrides legacy
``super_admin.Plan`` when resolving ``School.get_enabled_feature_codes()``.
"""
from __future__ import annotations

import logging

from django.core.cache import cache
from django.http import JsonResponse
from django_tenants.utils import get_public_schema_name, schema_context

logger = logging.getLogger(__name__)

FEATURES_CACHE_PREFIX = "school_plan_features:v1:"
FEATURES_CACHE_TTL = 120

# Product codes that grant the same capability (must stay in sync when saving plans).
MESSAGING_FEATURE_ALIASES = frozenset({"messaging", "platform_messaging"})


def get_allowed_features(school_id: int) -> list[str]:
    """Sorted canonical feature keys for the school's base plan (subscription → super_admin → Basic defaults)."""
    from apps.core import plan_features

    return plan_features.get_allowed_features(school_id)


def normalize_feature_key(feature_key: str) -> str:
    """Normalize API / UI keys (e.g. ``ATTENDANCE``) to DB codes (``attendance``)."""
    return (feature_key or "").strip().lower()


def expand_plan_feature_alias_codes(codes) -> set[str]:
    """
    If any code in an alias group is present, treat every alias in that group as enabled.

    Prevents ``messaging`` vs ``platform_messaging`` drift (plan UI vs route gates).
    """
    out = {normalize_feature_key(str(c)) for c in codes if c is not None and str(c).strip()}
    for group in (MESSAGING_FEATURE_ALIASES,):
        if out & group:
            out |= group
    return out


def plan_includes_feature(plan_codes, feature_code: str) -> bool:
    """Whether a base plan's code set includes a feature (legacy + canonical aware)."""
    from apps.core.plan_features import feature_granted, materialize_feature_set

    return feature_granted(materialize_feature_set(plan_codes), feature_code)


def sync_messaging_aliases_in_superadmin_plan_selection(
    selected_codes: set[str] | frozenset[str],
    valid_codes: set[str] | frozenset[str],
) -> set[str]:
    """When saving a super_admin Plan, keep messaging / platform_messaging toggles aligned."""
    out = {str(c).strip() for c in selected_codes if str(c).strip()}
    valid = {str(c).strip() for c in valid_codes if str(c).strip()}
    m, pm = "messaging", "platform_messaging"
    if (m in out) or (pm in out):
        if m in valid:
            out.add(m)
        if pm in valid:
            out.add(pm)
    else:
        out.discard(m)
        out.discard(pm)
    return out


def invalidate_school_feature_cache(school_id: int) -> None:
    """Invalidate cached enabled-feature sets for a school (call from signals)."""
    if not school_id:
        return
    cache.delete(f"{FEATURES_CACHE_PREFIX}{int(school_id)}")
    try:
        from apps.core.plan_route_gate import clear_route_feature_cache

        clear_route_feature_cache()
    except Exception:
        logger.debug("plan_route_gate cache clear skipped", exc_info=True)


def get_cached_enabled_feature_codes(school) -> frozenset:
    """Cross-request cache of ``school.get_enabled_feature_codes()`` (short TTL)."""
    key = f"{FEATURES_CACHE_PREFIX}{school.pk}"
    hit = cache.get(key)
    if hit is not None:
        return frozenset(hit)
    codes = frozenset(school.get_enabled_feature_codes())
    cache.set(key, list(codes), FEATURES_CACHE_TTL)
    return codes


def has_feature_access(school_id, feature_key: str) -> bool:
    """
    Return whether ``feature_key`` is allowed for the school (strict plan + materialized gates).

    ``school_id`` may be the school's integer primary key or its public ``code`` string
    (``User.school_id`` uses ``to_field="code"`` in some deployments).
    """
    from apps.core import plan_features

    return plan_features.has_feature_access(school_id, feature_key)


def invalidate_feature_cache_for_schools_on_superadmin_plan(superadmin_plan_pk: int) -> None:
    """After editing a super_admin Plan's features, drop cached feature sets for affected schools."""
    from apps.customers.models import School

    if not superadmin_plan_pk:
        return
    for sid in School.objects.filter(plan_id=int(superadmin_plan_pk)).values_list("pk", flat=True):
        invalidate_school_feature_cache(int(sid))


def plan_access_denied_json() -> JsonResponse:
    """Standard JSON body for APIs when a module is not on the school's plan (exact contract)."""
    return JsonResponse(
        {
            "status": "error",
            "message": "This feature is not available in your current plan. Please contact your school administrator.",
        },
        status=403,
    )
