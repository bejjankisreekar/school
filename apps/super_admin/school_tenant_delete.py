"""
Hard-delete a school tenant: public-schema users + PostgreSQL tenant schema (all tables/data) + School row.

Uses django-tenants TenantMixin.delete(force_drop=True), which runs DROP SCHEMA ... CASCADE.

`accounts_user.school_id` references `customers_school.code` (not pk). We match by live FK, raw code,
and common case variants so stale rows are removed. Superadmin accounts are never deleted.
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django_tenants.utils import get_public_schema_name

from apps.accounts.models import User as AccountUser
from apps.core.models import SchoolEnrollmentRequest
from apps.customers.models import School

logger = logging.getLogger(__name__)


class SchoolDeletionError(Exception):
    """User-visible reason school could not be deleted."""


def _school_code_variants(code: str) -> set[str]:
    base = (code or "").strip()
    if not base:
        return set()
    out = {base, base.upper(), base.lower()}
    return {c for c in out if c}


def _public_user_ids_for_school(school: School) -> list[int]:
    """All public `accounts_user` ids tied to this school (by FK or by stored school code)."""
    User = get_user_model()
    variants = _school_code_variants(school.code or "")
    q = Q(school__pk=school.pk)
    for v in variants:
        q |= Q(school_id=v)
    return list(
        User.objects.filter(q)
        .exclude(role=AccountUser.Roles.SUPERADMIN)
        .distinct()
        .values_list("pk", flat=True)
    )


def _clear_public_user_dependencies(user_ids: list[int]) -> None:
    """Remove rows that often block User.delete() on PostgreSQL."""
    if not user_ids:
        return
    try:
        from django.contrib.admin.models import LogEntry

        LogEntry.objects.filter(user_id__in=user_ids).delete()
    except Exception:
        logger.exception("School delete: admin LogEntry cleanup failed (non-fatal)")

    try:
        from rest_framework.authtoken.models import Token

        Token.objects.filter(user_id__in=user_ids).delete()
    except Exception:
        pass

    try:
        from apps.accounts.models import BlockedLoginAttempt

        BlockedLoginAttempt.objects.filter(user_id__in=user_ids).delete()
    except Exception:
        logger.exception("School delete: BlockedLoginAttempt cleanup failed (non-fatal)")


def _delete_users_by_ids(user_ids: list[int]) -> int:
    if not user_ids:
        return 0
    User = get_user_model()
    try:
        User.objects.filter(pk__in=user_ids).delete()
        return len(user_ids)
    except ProtectedError as exc:
        logger.exception("School delete: blocked deleting users (protected FK)")
        raise SchoolDeletionError(
            "Could not remove one or more user accounts because another table still references them. "
            "Check server logs for the protected relation."
        ) from exc


def _orphan_user_ids_for_codes(codes: set[str]) -> list[int]:
    """
    Users whose `school_id` still holds a tenant code but no `customers_school` row uses that code
    (e.g. after a partial delete or SET_NULL mismatch). Case-insensitive vs remaining schools.
    """
    if not codes:
        return []
    User = get_user_model()
    q = Q()
    for c in codes:
        q |= Q(school_id=c)
    existing_lower = {
        str(e).strip().lower()
        for e in School.objects.exclude(schema_name=get_public_schema_name()).values_list("code", flat=True)
        if e
    }
    out: list[int] = []
    for uid, sid in (
        User.objects.filter(q)
        .exclude(role=AccountUser.Roles.SUPERADMIN)
        .values_list("pk", "school_id")
    ):
        if sid is None or str(sid).strip() == "":
            continue
        if str(sid).strip().lower() not in existing_lower:
            out.append(uid)
    return out


def delete_school_with_tenant_schema(school: School) -> int:
    """
    Delete public users for this school (all matching rows), related audit rows, drop tenant schema,
    delete School, then sweep orphan users still holding this school code.

    Returns the number of `accounts_user` rows removed (primary delete + orphan sweep; best-effort).
    """
    sn = (school.schema_name or "").strip()
    public_schema = get_public_schema_name()
    if not sn or sn.lower() == public_schema.lower():
        raise SchoolDeletionError("Cannot delete the public platform tenant.")

    connection.set_schema_to_public()
    school_pk = school.pk
    code_variants = _school_code_variants(school.code or "")

    # Enrollment audit rows for this tenant (school FK); reviewed_by may point at school admins.
    try:
        n_er, _ = SchoolEnrollmentRequest.objects.filter(school_id=school_pk).delete()
        if n_er:
            logger.info("School delete: removed %s enrollment request row(s) for school pk=%s", n_er, school_pk)
    except Exception:
        logger.exception("School delete: enrollment request cleanup failed")

    user_ids = _public_user_ids_for_school(school)
    _clear_public_user_dependencies(user_ids)
    deleted_accounts = _delete_users_by_ids(user_ids)

    try:
        from apps.core.subscription_access import invalidate_school_feature_cache

        invalidate_school_feature_cache(school_pk)
    except Exception:
        logger.exception("invalidate_school_feature_cache failed (non-fatal) for school pk=%s", school_pk)

    try:
        school.delete(force_drop=True)
    except Exception as exc:
        logger.exception("School delete: school.delete(force_drop) failed pk=%s", school_pk)
        raise SchoolDeletionError(
            "Could not drop the tenant schema or remove the school row. "
            "See server logs for the database error (locks, permissions, or a stuck connection)."
        ) from exc

    connection.set_schema_to_public()
    logger.info("School delete: dropped schema %r and removed School pk=%s", sn, school_pk)

    orphan_ids = list(dict.fromkeys(_orphan_user_ids_for_codes(code_variants)))
    orphan_deleted = 0
    if orphan_ids:
        _clear_public_user_dependencies(orphan_ids)
        try:
            orphan_deleted = _delete_users_by_ids(orphan_ids)
        except SchoolDeletionError:
            logger.warning(
                "School delete: orphan user cleanup incomplete for codes %s (ids=%s)",
                code_variants,
                orphan_ids,
            )

    total_users_removed = deleted_accounts + orphan_deleted
    if orphan_deleted:
        logger.info(
            "School delete: removed %s user(s) in primary batch, %s orphan(s) by school code",
            deleted_accounts,
            orphan_deleted,
        )
    return total_users_removed
