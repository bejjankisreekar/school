from __future__ import annotations

import csv
import json
import logging
import math
import re
from contextlib import nullcontext
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.validators import URLValidator
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Exists, Max, Min, OuterRef, Q, Subquery, Sum
from django.db.utils import DatabaseError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from apps.accounts.decorators import superadmin_required
from apps.accounts.models import User as AccountUser

logger = logging.getLogger(__name__)
from apps.core.branding import get_platform_product_name
from apps.core.self_service_enrollment import seed_tenant_bootstrap
from apps.core.tenant_provisioning import (
    allocate_unique_schema_name,
    generate_unique_school_code_from_name,
    schema_name_for_school_code,
    validate_school_code_format,
)
from apps.customers.models import (
    DEFAULT_CORE_SCHOOL_FEATURES,
    Coupon,
    Domain,
    School,
    SchoolBillingAuditLog,
    SchoolFeatureAddon,
    SchoolGeneratedInvoice,
    SaaSPlatformPayment,
    PlatformInvoicePayment,
)

from .forms import BOARD_CHOICES, SuperAdminCreateSchoolForm, subscription_period_end
from .models import Feature, Plan, PlanName


def _tab(request, section: str | None = None) -> str:
    t = (section or request.GET.get("tab") or "overview").strip().lower()
    allowed = {"overview", "schools", "plans", "billing", "analytics", "settings"}
    return t if t in allowed else "overview"


def _seed_minimum_plans_and_features():
    """
    Ensures Basic/Pro/Premium exist. Features are editable in UI after creation.
    """
    for n in (PlanName.BASIC, PlanName.PRO, PlanName.PREMIUM):
        Plan.objects.get_or_create(name=n, defaults={"price": 0, "is_active": True})
    try:
        from apps.core.plan_features import seed_super_admin_tier_features

        seed_super_admin_tier_features()
    except Exception:
        pass


def _safe_tenant_counts(school: School) -> tuple[int, int]:
    """
    Returns (students_count, teachers_count) for a tenant schema.
    Safe for partially-migrated tenants.
    """
    try:
        from django_tenants.utils import tenant_context

        from apps.school_data.models import Student, Teacher

        with tenant_context(school):
            with transaction.atomic():
                return (Student.objects.count(), Teacher.objects.count())
    except DatabaseError:
        try:
            if not connection.in_atomic_block:
                connection.rollback()
        except Exception:
            pass
        return (0, 0)
    except Exception:
        return (0, 0)


@transaction.non_atomic_requests
@superadmin_required
@require_http_methods(["GET"])
def control_center_root(request):
    connection.set_schema_to_public()
    return redirect("core:super_admin:control_center_section", section="overview")


@transaction.non_atomic_requests
@superadmin_required
@require_http_methods(["GET", "POST"])
def control_center(request, section: str | None = None):
    """
    Single, centralized SaaS Control Center.
    Route: /super-admin/control-center/
    """
    connection.set_schema_to_public()
    _seed_minimum_plans_and_features()

    tab = _tab(request, section=section)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "save_plan_features":
            from apps.core.subscription_access import (
                invalidate_feature_cache_for_schools_on_superadmin_plan,
                sync_messaging_aliases_in_superadmin_plan_selection,
            )

            plan_id = request.POST.get("plan_id") or ""
            plan = get_object_or_404(Plan, pk=plan_id)
            selected = set(request.POST.getlist("features"))
            valid = set(Feature.objects.values_list("code", flat=True))
            cleaned = sync_messaging_aliases_in_superadmin_plan_selection(selected & valid, valid)
            plan.features.set(list(Feature.objects.filter(code__in=sorted(cleaned))))
            invalidate_feature_cache_for_schools_on_superadmin_plan(plan.pk)
            messages.success(request, f"Updated features for {plan.get_name_display()}.")
            return redirect("core:super_admin:control_center_section", section="plans")

        if action == "create_feature":
            name = (request.POST.get("name") or "").strip()
            code = (request.POST.get("code") or "").strip()
            category = (request.POST.get("category") or "").strip()
            if not name or not code:
                messages.error(request, "Feature name and code are required.")
                return redirect("core:super_admin:control_center_section", section="plans")
            Feature.objects.update_or_create(
                code=code,
                defaults={"name": name, "category": category or Feature._meta.get_field("category").default},
            )
            messages.success(request, f"Feature saved: {name}.")
            return redirect("core:super_admin:control_center_section", section="plans")

        if action == "update_school_quick":
            school_id = request.POST.get("school_id")
            raw_plan = (request.POST.get("plan_id") or "").strip()
            status = (request.POST.get("status") or "").strip()
            school = get_object_or_404(
                School.objects.exclude(schema_name="public").select_related("plan"),
                pk=school_id,
            )
            choices = {c[0] for c in School.SchoolStatus.choices}
            if status not in choices:
                messages.error(request, "Invalid status.")
                return redirect("core:super_admin:control_center_section", section="schools")
            update_fields: list[str] = []
            if raw_plan == "":
                if school.plan_id is not None:
                    old_plan_id = school.plan_id
                    old_label = ""
                    try:
                        old_label = school.plan.get_name_display()
                    except Exception:
                        old_label = str(old_plan_id)
                    school.plan = None
                    update_fields.append("plan")
                    SchoolBillingAuditLog.objects.create(
                        school=school,
                        kind=SchoolBillingAuditLog.Kind.PLAN_CHANGE,
                        summary="Plan cleared (no plan)",
                        payload={
                            "before_plan_id": old_plan_id,
                            "after_plan_id": None,
                            "before_plan_label": old_label,
                            "after_plan_label": "",
                        },
                        created_by=request.user if getattr(request.user, "is_authenticated", False) else None,
                    )
            else:
                plan = get_object_or_404(Plan, pk=raw_plan)
                if school.plan_id != plan.pk:
                    old_plan_id = school.plan_id
                    old_label = ""
                    if school.plan_id:
                        try:
                            old_label = school.plan.get_name_display()
                        except Exception:
                            old_label = str(school.plan_id)
                    school.plan = plan
                    update_fields.append("plan")
                    SchoolBillingAuditLog.objects.create(
                        school=school,
                        kind=SchoolBillingAuditLog.Kind.PLAN_CHANGE,
                        summary=f"Plan changed to {plan.get_name_display()}",
                        payload={
                            "before_plan_id": old_plan_id,
                            "after_plan_id": plan.pk,
                            "before_plan_label": old_label,
                            "after_plan_label": plan.get_name_display(),
                        },
                        created_by=request.user if getattr(request.user, "is_authenticated", False) else None,
                    )
            if school.school_status != status:
                school.school_status = status
                update_fields.append("school_status")
                if status in (School.SchoolStatus.ACTIVE, School.SchoolStatus.TRIAL) and not school.is_archived:
                    school.is_active = True
                    update_fields.append("is_active")
            if update_fields:
                school.save(update_fields=list(dict.fromkeys(update_fields)))
                if "plan" in update_fields:
                    from apps.core.subscription_access import invalidate_school_feature_cache

                    invalidate_school_feature_cache(school.pk)
                messages.success(request, f"{school.name}: saved.")
            else:
                messages.info(request, f"{school.name}: no changes.")
            return redirect("core:super_admin:control_center_section", section="schools")

        if action == "set_school_plan":
            school_id = request.POST.get("school_id")
            plan_id = request.POST.get("plan_id")
            school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
            plan = get_object_or_404(Plan, pk=plan_id)
            old_plan_id = school.plan_id
            old_label = ""
            if school.plan_id:
                try:
                    old_label = school.plan.get_name_display()
                except Exception:
                    old_label = str(school.plan_id)
            school.plan = plan
            school.save(update_fields=["plan"])
            from apps.core.subscription_access import invalidate_school_feature_cache

            invalidate_school_feature_cache(school.pk)
            if old_plan_id != plan.pk:
                SchoolBillingAuditLog.objects.create(
                    school=school,
                    kind=SchoolBillingAuditLog.Kind.PLAN_CHANGE,
                    summary=f"Plan changed to {plan.get_name_display()}",
                    payload={
                        "before_plan_id": old_plan_id,
                        "after_plan_id": plan.pk,
                        "before_plan_label": old_label,
                        "after_plan_label": plan.get_name_display(),
                    },
                    created_by=request.user if getattr(request.user, "is_authenticated", False) else None,
                )
            messages.success(request, f"{school.name}: plan set to {plan.get_name_display()}.")
            return redirect("core:super_admin:control_center_section", section="schools")

        if action == "set_school_status":
            school_id = request.POST.get("school_id")
            status = (request.POST.get("status") or "").strip()
            school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
            choices = {c[0] for c in School.SchoolStatus.choices}
            if status not in choices:
                messages.error(request, "Invalid status.")
                return redirect("core:super_admin:control_center_section", section="schools")
            school.school_status = status
            uf = ["school_status"]
            if status in (School.SchoolStatus.ACTIVE, School.SchoolStatus.TRIAL) and not school.is_archived:
                school.is_active = True
                uf.append("is_active")
            school.save(update_fields=uf)
            messages.success(request, f"{school.name}: status updated.")
            return redirect("core:super_admin:control_center_section", section="schools")

        if action == "archive_school":
            school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=request.POST.get("school_id"))
            school.is_archived = True
            school.save(update_fields=["is_archived", "is_active"])
            messages.success(request, f"{school.name}: archived (login blocked; data retained).")
            return redirect("core:super_admin:control_center_section", section="schools")

        if action == "unarchive_school":
            school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=request.POST.get("school_id"))
            school.is_archived = False
            uf = ["is_archived"]
            if school.school_status not in (School.SchoolStatus.SUSPENDED, School.SchoolStatus.INACTIVE):
                school.is_active = True
                uf.append("is_active")
            school.save(update_fields=uf)
            messages.success(request, f"{school.name}: unarchived.")
            return redirect("core:super_admin:control_center_section", section="schools")

        if action == "suspend_school_lifecycle":
            school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=request.POST.get("school_id"))
            school.is_archived = False
            school.school_status = School.SchoolStatus.SUSPENDED
            school.save(update_fields=["is_archived", "school_status", "is_active"])
            messages.success(request, f"{school.name}: suspended (all tenant logins blocked).")
            return redirect("core:super_admin:control_center_section", section="schools")

        if action == "restore_school_access":
            school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=request.POST.get("school_id"))
            school.is_archived = False
            school.school_status = School.SchoolStatus.ACTIVE
            school.is_active = True
            school.save(update_fields=["is_archived", "school_status", "is_active"])
            messages.success(request, f"{school.name}: access restored (active).")
            return redirect("core:super_admin:control_center_section", section="schools")

        if action == "school_soft_inactivate":
            school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=request.POST.get("school_id"))
            if school.is_archived or school.school_status == School.SchoolStatus.SUSPENDED:
                messages.error(request, "Unarchive or restore the school before using soft inactivate.")
                return redirect("core:super_admin:control_center_section", section="schools")
            school.is_active = False
            school.save(update_fields=["is_active"])
            messages.success(request, f"{school.name}: marked inactive (logins still allowed with a platform notice).")
            return redirect("core:super_admin:control_center_section", section="schools")

        if action == "school_soft_reactivate":
            school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=request.POST.get("school_id"))
            if school.school_status in (School.SchoolStatus.SUSPENDED, School.SchoolStatus.INACTIVE):
                messages.error(request, "Change lifecycle status first (this school is suspended or fully inactive).")
                return redirect("core:super_admin:control_center_section", section="schools")
            school.is_active = True
            school.save(update_fields=["is_active"])
            messages.success(request, f"{school.name}: reactivated (operational flag on).")
            return redirect("core:super_admin:control_center_section", section="schools")

        if action == "delete_school":
            from .school_tenant_delete import SchoolDeletionError, delete_school_with_tenant_schema

            school_id = (request.POST.get("school_id") or "").strip()
            confirm = (request.POST.get("delete_confirm_name") or "").strip()
            school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
            expected = (school.name or "").strip()
            if not expected or confirm != expected:
                messages.error(
                    request,
                    "Deletion cancelled: type the school name exactly as shown to confirm.",
                )
                return redirect("core:super_admin:control_center_section", section="schools")
            label = school.name
            try:
                users_removed = delete_school_with_tenant_schema(school)
            except SchoolDeletionError as exc:
                messages.error(request, str(exc))
                return redirect("core:super_admin:control_center_section", section="schools")
            except Exception:
                logger.exception("delete_school failed for school_id=%s", school_id)
                messages.error(
                    request,
                    "Could not delete that school. Check server logs (database locks, permissions, or a stuck migration).",
                )
                return redirect("core:super_admin:control_center_section", section="schools")
            messages.success(
                request,
                f"Deleted “{label}”: tenant schema dropped (all tenant tables and data removed), "
                f"linked enrollment audit rows removed, and {users_removed} public user account(s) removed "
                f"(including any orphan rows still holding that school code). Superadmin accounts were not touched.",
            )
            return redirect("core:super_admin:control_center_section", section="schools")

        if action == "save_school_feature_addons":
            from apps.core.subscription_access import plan_includes_feature

            school_id = (request.POST.get("school_id") or "").strip()
            school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
            plan_codes = school.get_base_plan_feature_codes()
            for feat in Feature.objects.all():
                code = feat.code
                if plan_includes_feature(plan_codes, code):
                    SchoolFeatureAddon.objects.filter(school=school, feature=feat).delete()
                    continue
                enabled = request.POST.get(f"addon_enabled__{code}") == "on"
                price_raw = (request.POST.get(f"addon_price__{code}") or "0").strip()
                notes = (request.POST.get(f"addon_notes__{code}") or "").strip()[:2000]
                try:
                    price = Decimal(price_raw or "0")
                except Exception:
                    price = Decimal("0")
                if price < 0:
                    price = Decimal("0")
                if enabled:
                    SchoolFeatureAddon.objects.update_or_create(
                        school=school,
                        feature=feat,
                        defaults={
                            "extra_monthly_charge": price,
                            "notes": notes,
                            "is_enabled": True,
                        },
                    )
                else:
                    SchoolFeatureAddon.objects.filter(school=school, feature=feat).update(
                        is_enabled=False,
                        extra_monthly_charge=price,
                        notes=notes,
                    )
            messages.success(request, f"{school.name}: add-on features saved.")
            base = reverse("core:super_admin:control_center_section", kwargs={"section": "plans"})
            return redirect(f"{base}?addon_school={school.pk}")

        messages.error(request, "Invalid action.")
        return redirect("core:super_admin:control_center_section", section=tab)

    plans = list(Plan.objects.filter(is_active=True).prefetch_related("features"))
    features = list(Feature.objects.all().order_by("category", "name"))
    feature_groups = None
    comparison = None
    if tab == "plans":
        # Group features by category for cleaner UI.
        order = ["academic", "operations", "exams", "communication", "finance"]
        by_cat = {k: [] for k in order}
        extra = []
        for f in features:
            c = (getattr(f, "category", "") or "").strip().lower()
            if c in by_cat:
                by_cat[c].append(f)
            else:
                extra.append(f)
        feature_groups = [(k, by_cat[k]) for k in order if by_cat[k]]
        if extra:
            feature_groups.append(("other", extra))

        # Comparison matrix: feature rows × plan columns (fast set lookup).
        by_name = {p.name: p for p in plans}
        cols = [by_name.get(PlanName.BASIC), by_name.get(PlanName.PRO), by_name.get(PlanName.PREMIUM)]
        cols = [p for p in cols if p is not None]
        plan_cols = [{"id": p.id, "key": p.name, "label": p.get_name_display()} for p in cols]
        plan_feature_codes = {p.id: set(p.features.values_list("code", flat=True)) for p in cols}

        comparison = {
            "plan_cols": plan_cols,
            "plan_feature_codes": plan_feature_codes,
            "feature_groups": feature_groups,
        }

    schools_base_qs = (
        School.objects.exclude(schema_name="public")
        .select_related("plan")
        .prefetch_related("feature_addons__feature")
    )
    lifecycle_filter = (request.GET.get("lifecycle") or "all").strip().lower()
    if tab == "schools":
        sch_qs = schools_base_qs
        if lifecycle_filter == "active":
            sch_qs = sch_qs.filter(
                is_archived=False,
                is_active=True,
                school_status__in=(School.SchoolStatus.ACTIVE, School.SchoolStatus.TRIAL),
            )
        elif lifecycle_filter == "inactive":
            sch_qs = (
                schools_base_qs.filter(is_archived=False)
                .exclude(school_status=School.SchoolStatus.SUSPENDED)
                .filter(Q(is_active=False) | Q(school_status=School.SchoolStatus.INACTIVE))
            )
        elif lifecycle_filter == "suspended":
            sch_qs = schools_base_qs.filter(school_status=School.SchoolStatus.SUSPENDED)
        elif lifecycle_filter == "archived":
            sch_qs = schools_base_qs.filter(is_archived=True)
        schools = list(sch_qs.order_by("name")[:500])
    else:
        schools = list(schools_base_qs.order_by("name")[:200])

    addon_school = None
    addon_matrix = None
    if tab == "plans":
        addon_id = (request.GET.get("addon_school") or "").strip()
        if addon_id.isdigit():
            try:
                addon_school = (
                    School.objects.exclude(schema_name="public")
                    .select_related("plan")
                    .prefetch_related("feature_addons__feature", "plan__features")
                    .get(pk=int(addon_id))
                )
            except School.DoesNotExist:
                addon_school = None
        if addon_school:
            from apps.core.subscription_access import plan_includes_feature

            plan_codes = addon_school.get_base_plan_feature_codes()
            addons_by_code = {
                a.feature.code: a for a in addon_school.feature_addons.all() if a.feature_id
            }
            addon_matrix = [
                {
                    "feature": f,
                    "in_plan": plan_includes_feature(plan_codes, f.code),
                    "addon": addons_by_code.get(f.code),
                }
                for f in features
            ]

    # Metrics (overview only — tenant queries can be expensive)
    total_schools = active_schools = trial_schools = 0
    total_students = total_teachers = 0
    plan_counts = {"basic": 0, "pro": 0, "premium": 0}
    recent_schools = []

    billing_cards = None
    billing_total_mrr_monthly = None
    billing_dashboard = None
    billing_api_schools_url = None
    billing_export_csv_url = None
    billing_api_send_reminder_url = None
    billing_api_coupons_url = None
    billing_month_choices: list[dict[str, str | int]] = []
    billing_period_mmm_options: list[dict[str, str]] = []
    billing_period_mmm_options_json = "[]"
    if tab == "billing":
        billing_api_schools_url = reverse("core:super_admin:billing_api_schools")
        billing_export_csv_url = reverse("core:super_admin:billing_export_csv")
        billing_api_send_reminder_url = reverse("core:super_admin:billing_api_send_reminder")
        billing_api_coupons_url = reverse("core:super_admin:billing_api_coupons")
        base_qs = (
            School.objects.exclude(schema_name="public")
            .select_related("plan")
            .prefetch_related("feature_addons__feature")
        )
        latest_audit_sq = Subquery(
            SchoolBillingAuditLog.objects.filter(school_id=OuterRef("pk"))
            .order_by("-created_at")
            .values("created_at")[:1]
        )
        pending_inv_exists = Exists(
            SchoolGeneratedInvoice.objects.filter(
                school_id=OuterRef("pk"),
                status=SchoolGeneratedInvoice.Status.ISSUED,
            )
        )
        ann_schools = list(
            base_qs.annotate(
                last_billing_at=latest_audit_sq,
                has_pending_invoice=pending_inv_exists,
            ).order_by("name")[:500]
        )
        school_ids = [s.pk for s in ann_schools]
        pending_agg = {r["school_id"]: r for r in SchoolGeneratedInvoice.objects.filter(
            status=SchoolGeneratedInvoice.Status.ISSUED,
            school_id__in=school_ids,
        ).values("school_id").annotate(c=Count("id"), amt=Sum("grand_total"))}
        last_pay_map = _billing_last_payment_dates_bulk(school_ids)
        oldest_issued_map = _billing_oldest_issued_invoice_bulk(school_ids)
        overdue_cut = timezone.now() - timedelta(days=_billing_invoice_overdue_days())
        period_opts = _billing_mmm_yyyy_period_options()
        period_vals = {o["value"] for o in period_opts}
        billing_period_mmm_options = period_opts
        billing_period_mmm_options_json = json.dumps(period_opts)
        card_rows = []
        mrr_sum = Decimal("0")
        active_ct = 0
        total_students_live = 0
        pending_amount_all = Decimal("0")
        pending_inv_all = 0
        for sch in ann_schools:
            stu, _ = _safe_tenant_counts(sch)
            bd = sch.saas_billing_monthly_breakdown(stu)
            mrr_sum += Decimal(bd["final_monthly"])
            if sch.school_status == School.SchoolStatus.ACTIVE:
                active_ct += 1
            total_students_live += int(bd["tenant_student_count"])
            pa = pending_agg.get(sch.pk) or {}
            pc = int(pa.get("c") or 0)
            pamt = pa.get("amt") or Decimal("0")
            pending_inv_all += pc
            pending_amount_all += Decimal(pamt or 0)
            plan_slug = ""
            if sch.plan_id:
                plan_slug = str(sch.plan.name or "")
            last_ts = getattr(sch, "last_billing_at", None)
            oldest = oldest_issued_map.get(sch.pk)
            pend_over = bool(oldest and oldest < overdue_cut)
            billing_obj = _billing_nested_billing_dict(
                sch,
                bd,
                stu,
                last_payment=last_pay_map.get(sch.pk),
                pending_overdue=pend_over,
            )
            comp_waived, _ = _billing_complimentary_waiver_active(sch)
            has_pend = bool(getattr(sch, "has_pending_invoice", False))
            cy0 = timezone.localdate().year
            ip = _billing_invoice_period_payload(sch)
            val_mmm = ""
            is_yearly_card = sch.saas_billing_cycle == School.SaaSBillingCycle.YEARLY
            sch_period_y = int(ip["year"])
            sch_period_m: int | None = int(ip["month"]) if ip.get("month") is not None else None
            if sch.saas_billing_cycle == School.SaaSBillingCycle.MONTHLY and ip.get("month") is not None:
                val_mmm = f"{ip['year']:04d}-{int(ip['month']):02d}"
                if val_mmm not in period_vals:
                    val_mmm = period_opts[len(period_opts) // 2]["value"]
                try:
                    py_s, pm_s, *_ = val_mmm.split("-", 2)
                    sch_period_y, sch_period_m = int(py_s), int(pm_s)
                except (ValueError, IndexError):
                    sch_period_m = int(ip["month"])
            sch_free_sel = _billing_period_starts_before_commencement(
                sch,
                sch_period_y,
                sch_period_m if not is_yearly_card else None,
                is_yearly_card,
            )
            eff_bs_card = _effective_billing_start_date(sch)
            card_rows.append(
                {
                    "school": sch,
                    "students": stu,
                    "breakdown": bd,
                    "plan_slug": plan_slug,
                    "last_billing_at": last_ts,
                    "last_billing_at_iso": last_ts.isoformat() if last_ts else "",
                    "has_pending_invoice": has_pend,
                    "pending_invoice_count": pc,
                    "pending_invoice_total": pamt if pamt is not None else Decimal("0"),
                    "pending_payment_overdue": pend_over,
                    "billing": billing_obj,
                    "mrr_dec": Decimal(bd["final_monthly"]),
                    "complimentary_waiver_active": comp_waived,
                    "card_invoice_period": ip,
                    "card_billing_status": _billing_card_billing_surface(
                        sch, has_pend, schedule_period_free=sch_free_sel
                    ),
                    "card_year_choices": list(range(cy0 - 4, cy0 + 5)),
                    "card_period_monthly_value": val_mmm,
                    "card_effective_billing_start": eff_bs_card.isoformat() if eff_bs_card else "",
                    "card_period_y": sch_period_y,
                    "card_period_m": sch_period_m,
                }
            )
        n_cards = len(card_rows)
        if n_cards:
            sorted_mrr = sorted((r["mrr_dec"] for r in card_rows), reverse=True)
            k = max(1, math.ceil(n_cards * 0.2))
            hr_thr = sorted_mrr[k - 1]
        else:
            hr_thr = None
        billing_cards = []
        for r in card_rows:
            r["high_revenue"] = hr_thr is not None and r["mrr_dec"] >= hr_thr
            del r["mrr_dec"]
            billing_cards.append(r)
        billing_total_mrr_monthly = mrr_sum
        billing_dashboard = {
            "total_revenue_monthly": format(mrr_sum.quantize(Decimal("0.01")), "f"),
            "active_schools": active_ct,
            "pending_payments_amount": format(pending_amount_all.quantize(Decimal("0.01")), "f"),
            "pending_invoices_count": pending_inv_all,
            "total_students": total_students_live,
        }
        billing_month_choices = [{"value": i + 1, "name": _BILLING_MONTH_NAMES[i]} for i in range(12)]

    analytics_config_json = "{}"
    if tab == "analytics":
        period_opts = _billing_mmm_yyyy_period_options()
        analytics_config_json = json.dumps(
            {
                "monthOptions": period_opts,
                "billingSchoolUrlZero": reverse(
                    "core:super_admin:billing_school_detail", kwargs={"school_id": 0}
                ),
                "urls": {
                    "summary": reverse("core:super_admin:analytics_summary"),
                    "revenueTrend": reverse("core:super_admin:analytics_revenue_trend"),
                    "schoolRevenue": reverse("core:super_admin:analytics_school_revenue"),
                    "paymentStatus": reverse("core:super_admin:analytics_payment_status"),
                    "growth": reverse("core:super_admin:analytics_growth"),
                    "monthCollection": reverse("core:super_admin:analytics_month_collection"),
                    "planDistribution": reverse("core:super_admin:analytics_plan_distribution"),
                    "topRisk": reverse("core:super_admin:analytics_top_risk"),
                    "exportCsv": reverse("core:super_admin:analytics_export_csv"),
                },
            },
            default=str,
        )

    settings_config_json = "{}"
    if tab == "settings":
        settings_config_json = json.dumps(
            {
                "urls": {
                    "get": reverse("core:super_admin:control_center_settings_get"),
                    "update": reverse("core:super_admin:control_center_settings_update"),
                    "reset": reverse("core:super_admin:control_center_settings_reset"),
                },
            },
            default=str,
        )

    if tab == "overview":
        qs = School.objects.exclude(schema_name="public")
        total_schools = qs.count()
        active_schools = qs.filter(school_status=School.SchoolStatus.ACTIVE).count()
        trial_schools = qs.filter(school_status=School.SchoolStatus.TRIAL).count()
        plan_counts = {
            "basic": qs.filter(plan__name=PlanName.BASIC).count(),
            "pro": qs.filter(plan__name=PlanName.PRO).count(),
            "premium": qs.filter(plan__name=PlanName.PREMIUM).count(),
        }
        recent_schools = list(qs.order_by("-created_on")[:8])

        for sch in qs.only("id", "schema_name").order_by("name"):
            s, t = _safe_tenant_counts(sch)
            total_students += int(s)
            total_teachers += int(t)

    schools_billing_config_post_url = None
    schools_billing_config_get_url = None
    schools_dashboard = None
    school_cards: list[dict] = []
    if tab == "schools":
        from apps.core.platform_financials import _safe_tenant_footprint

        schools_billing_config_post_url = reverse("core:super_admin:schools_api_update_billing_config")
        schools_billing_config_get_url = reverse("core:super_admin:schools_api_list")
        agg = School.objects.exclude(schema_name="public").aggregate(
            total=Count("id"),
            active=Count(
                "id",
                filter=Q(
                    is_archived=False,
                    is_active=True,
                    school_status__in=(School.SchoolStatus.ACTIVE, School.SchoolStatus.TRIAL),
                ),
            ),
            trial=Count("id", filter=Q(school_status=School.SchoolStatus.TRIAL, is_archived=False)),
            suspended=Count("id", filter=Q(school_status=School.SchoolStatus.SUSPENDED)),
            archived=Count("id", filter=Q(is_archived=True)),
            inactive=Count(
                "id",
                filter=Q(is_archived=False)
                & ~Q(school_status=School.SchoolStatus.SUSPENDED)
                & (Q(is_active=False) | Q(school_status=School.SchoolStatus.INACTIVE)),
            ),
        )
        schools_dashboard = {
            "total": int(agg["total"] or 0),
            "active": int(agg["active"] or 0),
            "trial": int(agg["trial"] or 0),
            "suspended": int(agg["suspended"] or 0),
            "archived": int(agg["archived"] or 0),
            "inactive": int(agg["inactive"] or 0),
        }
        codes_for_admin = [s.code for s in schools]
        admin_names_by_code: dict[str, str] = {}
        for u in (
            AccountUser.objects.filter(role=AccountUser.Roles.ADMIN, school_id__in=codes_for_admin)
            .order_by("id")
        ):
            cid = u.school_id
            if cid and cid not in admin_names_by_code:
                admin_names_by_code[cid] = (u.get_full_name() or u.username).strip() or u.username

        for s in schools:
            t_ct, n_stu, n_cls = _safe_tenant_footprint(s)
            if s.plan_id:
                plan_label = (
                    "Premium · Enterprise"
                    if s.plan.name == PlanName.PREMIUM
                    else s.plan.get_name_display()
                )
            else:
                plan_label = "No plan"
            school_cards.append(
                {
                    "school": s,
                    "teachers": t_ct,
                    "students": n_stu,
                    "classes": n_cls,
                    "plan_label": plan_label,
                    "admin_display": admin_names_by_code.get(s.code) or "—",
                }
            )

    tmpl = {
        "overview": "super_admin/pages/overview.html",
        "schools": "super_admin/pages/schools.html",
        "plans": "super_admin/pages/plans.html",
        "billing": "super_admin/pages/billing.html",
        "analytics": "super_admin/pages/analytics.html",
        "settings": "super_admin/pages/settings.html",
    }.get(tab, "super_admin/pages/overview.html")

    return render(
        request,
        tmpl,
        {
            "tab": tab,
            "plans": plans,
            "features": features,
            "schools": schools,
            "total_schools": total_schools,
            "active_schools": active_schools,
            "trial_schools": trial_schools,
            "total_students": total_students,
            "total_teachers": total_teachers,
            "plan_counts": plan_counts,
            "recent_schools": recent_schools,
            "feature_groups": feature_groups,
            "comparison": comparison,
            "addon_school": addon_school,
            "addon_matrix": addon_matrix,
            "billing_cards": billing_cards,
            "billing_total_mrr_monthly": billing_total_mrr_monthly,
            "billing_dashboard": billing_dashboard,
            "billing_api_schools_url": billing_api_schools_url,
            "billing_export_csv_url": billing_export_csv_url,
            "billing_api_send_reminder_url": billing_api_send_reminder_url,
            "billing_api_coupons_url": billing_api_coupons_url,
            "schools_dashboard": schools_dashboard,
            "billing_month_choices": billing_month_choices,
            "billing_period_mmm_options": billing_period_mmm_options,
            "billing_period_mmm_options_json": billing_period_mmm_options_json,
            "schools_billing_config_post_url": schools_billing_config_post_url,
            "schools_billing_config_get_url": schools_billing_config_get_url,
            "school_cards": school_cards,
            "lifecycle_filter": lifecycle_filter,
            "analytics_config_json": analytics_config_json,
            "settings_config_json": settings_config_json,
        },
    )


def _derive_unique_username_from_email(email: str) -> str:
    UserModel = get_user_model()
    local = (email or "").split("@", 1)[0].strip()
    base = re.sub(r"[^a-zA-Z0-9_]", "_", local).strip("_")[:100] or "admin"
    candidate = base[:150]
    n = 0
    while UserModel.objects.filter(username=candidate).exists():
        n += 1
        suff = f"_{n}"
        candidate = (base[: 150 - len(suff)] + suff)
    return candidate


def _compose_school_address_lines(data: dict) -> str:
    lines = []
    soc = (data.get("society_registered_name") or "").strip()
    if soc:
        lines.append(f"Society / registered name: {soc}")
    if (data.get("address_line1") or "").strip():
        lines.append(data["address_line1"].strip())
    if (data.get("address_line2") or "").strip():
        lines.append(data["address_line2"].strip())
    loc = ", ".join(
        str(x).strip()
        for x in [
            data.get("city"),
            data.get("state"),
            data.get("pincode"),
            data.get("country"),
        ]
        if x and str(x).strip()
    )
    if loc:
        lines.append(loc)
    notes = (data.get("enrollment_notes") or "").strip()
    if notes:
        lines.append(notes)
    return "\n".join(lines).strip()


def _store_onboarding_documents(request, school_code: str) -> list[str]:
    """Save optional multi-file uploads; returns relative storage paths."""
    rel_paths: list[str] = []
    max_bytes = 5 * 1024 * 1024
    for f in request.FILES.getlist("documents"):
        if not getattr(f, "name", None):
            continue
        if getattr(f, "size", 0) and f.size > max_bytes:
            raise ValueError(f"Document '{f.name}' exceeds 5 MB.")
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", f.name)[:180] or "document"
        key = f"school_onboarding/{school_code}/{safe_name}"
        default_storage.save(key, ContentFile(f.read()))
        rel_paths.append(key)
    return rel_paths


def _create_school_tenant_from_superadmin_form(request, form: SuperAdminCreateSchoolForm) -> tuple[School, AccountUser]:
    """
    Persist School (tenant + schema), Domain, optional logo/docs in meta, and primary school admin user.
    Caller must use @transaction.non_atomic_requests (School.save triggers migrate_schemas).
    """
    connection.set_schema_to_public()
    data = form.cleaned_data
    UserModel = get_user_model()

    auto_code = bool(data.get("auto_generate_school_code"))
    if auto_code:
        code = generate_unique_school_code_from_name(data["school_name"])
    else:
        code = validate_school_code_format(data["school_code"])
        if School.objects.filter(code=code).exists():
            raise ValueError("School code already exists.")

    schema_name = schema_name_for_school_code(code)
    if School.objects.filter(schema_name=schema_name).exists():
        schema_name = allocate_unique_schema_name(schema_name)

    username = _derive_unique_username_from_email(data["admin_email"])
    if UserModel.objects.filter(username=username).exists():
        raise ValueError("Could not allocate a unique username for this admin email.")

    start: date = data["subscription_start_date"]
    cycle = data["billing_cycle"]
    period_end = subscription_period_end(start, cycle)

    meta: dict = {}
    meta.update(
        {
            "school_type": data.get("school_type") or "",
            "medium": data.get("medium") or "",
            "classes_offered": list(data.get("classes_available") or []),
            "student_capacity": data.get("student_capacity"),
            "teacher_capacity": data.get("teacher_capacity"),
            "alternate_phone": data.get("alternate_phone") or "",
            "subscription_start_date": start.isoformat(),
            "subscription_end_date": period_end.isoformat(),
            "created_via": "super_admin_control_center",
            "society_registered_name": (data.get("society_registered_name") or "").strip(),
        }
    )

    names = (data.get("admin_name") or "").strip().split()
    first = names[0] if names else ""
    last = " ".join(names[1:])[:150] if len(names) > 1 else ""

    addr = _compose_school_address_lines(data)
    est = data.get("established_year")
    doe = date(int(est), 1, 1) if est else None

    school = School(
        name=data["school_name"].strip(),
        code=code,
        schema_name=schema_name,
        contact_email=data["contact_email"].strip(),
        phone=data["phone"],
        address=addr,
        contact_person=data["admin_name"].strip(),
        website=(data.get("website") or "").strip()[:500],
        board_affiliation=(dict(BOARD_CHOICES).get(data.get("board"), data.get("board")) or "")[:120],
        date_of_establishment=doe,
        school_status=School.SchoolStatus.ACTIVE,
        plan=data["plan"],
        saas_billing_cycle=cycle,
        saas_service_start_date=start,
        billing_start_date=start,
        platform_control_meta=meta,
    )
    logo = data.get("logo")
    if logo:
        school.logo = logo

    school.save()

    if not Domain.objects.filter(tenant=school).exists():
        Domain.objects.create(
            domain=f"{school.schema_name}.localhost",
            tenant=school,
            is_primary=True,
        )

    try:
        doc_paths = _store_onboarding_documents(request, school.code)
    except ValueError:
        raise
    except Exception:
        doc_paths = []
    if doc_paths:
        meta2 = dict(school.platform_control_meta or {})
        meta2["onboarding_documents"] = doc_paths
        school.platform_control_meta = meta2
        school.save(update_fields=["platform_control_meta"])

    try:
        seed_tenant_bootstrap(school)
    except Exception:
        pass

    user = UserModel(
        username=username,
        email=data["admin_email"].strip(),
        role=AccountUser.Roles.ADMIN,
        school=school,
        is_active=True,
        is_staff=False,
        first_name=first[:150],
        last_name=last,
        phone_number=data["admin_phone"][:20],
    )
    user.set_password(data["password2"])
    try:
        user.save()
    except IntegrityError as exc:
        raise ValueError(
            "Could not create the admin user (username or account conflict). Try a different admin email."
        ) from exc
    return school, user


@transaction.non_atomic_requests
@superadmin_required
@require_http_methods(["GET", "POST"])
def control_center_school_create(request):
    """Full-page form: create tenant school + school admin (Super Admin only)."""
    connection.set_schema_to_public()
    _seed_minimum_plans_and_features()

    if request.method == "POST":
        form = SuperAdminCreateSchoolForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                _create_school_tenant_from_superadmin_form(request, form)
            except ValueError as exc:
                messages.error(request, str(exc))
            except Exception as exc:
                if settings.DEBUG:
                    messages.error(request, f"Could not create school: {exc}")
                else:
                    messages.error(
                        request,
                        "Could not create the school. Check logs or try again with a different code or username.",
                    )
            else:
                messages.success(request, "School Created Successfully")
                return redirect("core:super_admin:control_center_section", section="schools")
    else:
        form = SuperAdminCreateSchoolForm(
            initial={
                "subscription_start_date": timezone.localdate(),
                "billing_cycle": School.SaaSBillingCycle.MONTHLY,
                "country": "India",
            }
        )

    return render(
        request,
        "super_admin/pages/school_create.html",
        {
            "form": form,
            "tab": "schools",
            "schools_list_url": reverse("core:super_admin:control_center_section", kwargs={"section": "schools"}),
        },
    )


def _school_plan_label(school: School) -> str:
    if not school.plan_id:
        return "No plan"
    if school.plan.name == PlanName.PREMIUM:
        return "Premium · Enterprise"
    return school.plan.get_name_display()


@transaction.non_atomic_requests
@superadmin_required
@require_http_methods(["GET", "POST"])
def school_master_detail(request, school_id: int):
    """Full-page school control panel (master–detail)."""
    connection.set_schema_to_public()
    _seed_minimum_plans_and_features()
    school = get_object_or_404(
        School.objects.exclude(schema_name="public").select_related(
            "plan", "billing_plan", "subscription_plan"
        ),
        pk=school_id,
    )
    detail_url = reverse("core:super_admin:school_master_detail", kwargs={"school_id": school.pk})
    schools_list_url = reverse("core:super_admin:control_center_section", kwargs={"section": "schools"})

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        user = request.user

        if action == "update_school_profile":
            school.name = (request.POST.get("name") or school.name).strip()[:255]
            school.address = (request.POST.get("address") or "").strip()
            school.contact_email = (request.POST.get("contact_email") or "").strip()[:254]
            school.phone = (request.POST.get("phone") or "").strip()[:20]
            school.contact_person = (request.POST.get("contact_person") or "").strip()[:200]
            it = (request.POST.get("institution_type") or "").strip()
            if it not in {c[0] for c in School.InstitutionType.choices}:
                messages.error(request, "Invalid institution type.")
                return redirect(detail_url)
            school.institution_type = it
            wr = (request.POST.get("website") or "").strip()[:500]
            if wr:
                candidates = [wr]
                if "://" not in wr:
                    candidates.append(f"https://{wr}")
                stored = None
                for candidate in candidates:
                    try:
                        URLValidator()(candidate)
                        stored = candidate
                        break
                    except DjangoValidationError:
                        continue
                if stored is None:
                    messages.error(request, "Invalid website URL (leave blank if none).")
                    return redirect(detail_url)
                school.website = stored
            else:
                school.website = ""
            doe_raw = (request.POST.get("date_of_establishment") or "").strip()
            school.date_of_establishment = parse_date(doe_raw) if doe_raw else None
            school.registration_number = (request.POST.get("registration_number") or "").strip()[:120]
            school.board_affiliation = (request.POST.get("board_affiliation") or "").strip()[:120]
            school.save(
                update_fields=[
                    "name",
                    "address",
                    "contact_email",
                    "phone",
                    "contact_person",
                    "institution_type",
                    "website",
                    "date_of_establishment",
                    "registration_number",
                    "board_affiliation",
                ]
            )
            messages.success(request, "Organization & contacts saved.")
            return redirect(detail_url)

        if action == "update_school_branding":
            school.header_text = (request.POST.get("header_text") or "").strip()[:200]
            tc = (request.POST.get("theme_color") or "").strip()[:20]
            school.theme_color = tc if tc else "#4F46E5"
            pf = (request.POST.get("payslip_format") or "").strip()
            if pf not in {c[0] for c in School.PayslipFormat.choices}:
                pf = School.PayslipFormat.CORPORATE
            school.payslip_format = pf
            if (request.POST.get("clear_logo") or "").strip() == "1":
                if school.logo:
                    school.logo.delete(save=False)
                school.logo = None
            elif request.FILES.get("logo"):
                school.logo = request.FILES["logo"]
            school.save(update_fields=["header_text", "theme_color", "payslip_format", "logo"])
            messages.success(request, "Branding saved.")
            return redirect(detail_url)

        if action == "update_school_technical":
            school.custom_domain = (request.POST.get("custom_domain") or "").strip()[:255]
            school.is_single_tenant = (request.POST.get("is_single_tenant") or "").strip() in (
                "1",
                "true",
                "on",
                "yes",
            )
            raw_tid = (request.POST.get("timetable_current_profile_id") or "").strip()
            if raw_tid:
                try:
                    school.timetable_current_profile_id = int(raw_tid)
                except (TypeError, ValueError):
                    messages.error(request, "Timetable profile id must be a whole number or blank.")
                    return redirect(detail_url)
            else:
                school.timetable_current_profile_id = None
            school.save(
                update_fields=["custom_domain", "is_single_tenant", "timetable_current_profile_id"]
            )
            messages.success(request, "Technical settings saved.")
            return redirect(detail_url)

        if action == "update_school_saas_terms":
            cycle = (request.POST.get("saas_billing_cycle") or "").strip()
            if cycle not in {c[0] for c in School.SaaSBillingCycle.choices}:
                messages.error(request, "Invalid billing cycle.")
                return redirect(detail_url)
            school.saas_billing_cycle = cycle
            for fname, post_key in (
                ("saas_service_start_date", "saas_service_start_date"),
                ("saas_billing_complimentary_until", "saas_billing_complimentary_until"),
                ("trial_end_date", "trial_end_date"),
            ):
                raw_d = (request.POST.get(post_key) or "").strip()
                setattr(school, fname, parse_date(raw_d) if raw_d else None)
            school.saas_billing_auto_renew = (request.POST.get("saas_billing_auto_renew") or "").strip() in (
                "1",
                "true",
                "on",
                "yes",
            )
            school.save(
                update_fields=[
                    "saas_billing_cycle",
                    "saas_service_start_date",
                    "saas_billing_complimentary_until",
                    "trial_end_date",
                    "saas_billing_auto_renew",
                ]
            )
            messages.success(request, "SaaS billing terms saved.")
            return redirect(detail_url)

        if action == "update_school_saas_charges":
            extra = _parse_money(request.POST.get("billing_extra_per_student_month"))
            conc = _parse_money(request.POST.get("billing_concession_per_student_month"))
            if extra is None or conc is None:
                messages.error(request, "Extra and concession must be valid amounts.")
                return redirect(detail_url)
            if extra < 0 or conc < 0:
                messages.error(request, "Amounts cannot be negative.")
                return redirect(detail_url)
            school.billing_extra_per_student_month = extra
            school.billing_concession_per_student_month = conc
            raw_ov = (request.POST.get("billing_student_count_override") or "").strip()
            if raw_ov == "":
                school.billing_student_count_override = None
            else:
                try:
                    n = int(raw_ov)
                    if n < 0:
                        raise ValueError
                    school.billing_student_count_override = n
                except ValueError:
                    messages.error(request, "Student override must be a non‑negative integer or blank.")
                    return redirect(detail_url)
            school.save(
                update_fields=[
                    "billing_extra_per_student_month",
                    "billing_concession_per_student_month",
                    "billing_student_count_override",
                ]
            )
            messages.success(request, "Per‑student charges & headcount override saved.")
            return redirect(detail_url)

        if action == "update_platform_control_meta":
            raw = (request.POST.get("platform_control_meta") or "").strip()
            if raw == "":
                school.platform_control_meta = {}
            else:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    messages.error(request, "Control meta must be valid JSON.")
                    return redirect(detail_url)
                if not isinstance(parsed, dict):
                    messages.error(request, "Control meta must be a JSON object (e.g. {}).")
                    return redirect(detail_url)
                school.platform_control_meta = parsed
            school.save(update_fields=["platform_control_meta"])
            messages.success(request, "Platform control meta saved.")
            return redirect(detail_url)

        if action == "set_school_plan":
            from apps.core.subscription_access import invalidate_school_feature_cache

            raw_plan = (request.POST.get("plan_id") or "").strip()
            if raw_plan == "":
                if school.plan_id is not None:
                    old_plan_id = school.plan_id
                    old_label = ""
                    try:
                        old_label = school.plan.get_name_display()
                    except Exception:
                        old_label = str(old_plan_id)
                    school.plan = None
                    school.save(update_fields=["plan"])
                    invalidate_school_feature_cache(school.pk)
                    SchoolBillingAuditLog.objects.create(
                        school=school,
                        kind=SchoolBillingAuditLog.Kind.PLAN_CHANGE,
                        summary="Plan cleared (no plan)",
                        payload={
                            "before_plan_id": old_plan_id,
                            "after_plan_id": None,
                            "before_plan_label": old_label,
                            "after_plan_label": "",
                        },
                        created_by=user if getattr(user, "is_authenticated", False) else None,
                    )
                    messages.success(request, "Plan cleared.")
                else:
                    messages.info(request, "Already on no plan.")
            else:
                plan = get_object_or_404(Plan, pk=raw_plan)
                if school.plan_id != plan.pk:
                    old_plan_id = school.plan_id
                    old_label = ""
                    if school.plan_id:
                        try:
                            old_label = school.plan.get_name_display()
                        except Exception:
                            old_label = str(old_plan_id)
                    school.plan = plan
                    school.save(update_fields=["plan"])
                    invalidate_school_feature_cache(school.pk)
                    SchoolBillingAuditLog.objects.create(
                        school=school,
                        kind=SchoolBillingAuditLog.Kind.PLAN_CHANGE,
                        summary=f"Plan changed to {plan.get_name_display()}",
                        payload={
                            "before_plan_id": old_plan_id,
                            "after_plan_id": plan.pk,
                            "before_plan_label": old_label,
                            "after_plan_label": plan.get_name_display(),
                        },
                        created_by=user if getattr(user, "is_authenticated", False) else None,
                    )
                    messages.success(request, f"Plan set to {plan.get_name_display()}.")
                else:
                    messages.info(request, "Plan unchanged.")
            return redirect(detail_url)

        if action == "set_school_status":
            status = (request.POST.get("status") or "").strip()
            choices = {c[0] for c in School.SchoolStatus.choices}
            if status not in choices:
                messages.error(request, "Invalid status.")
                return redirect(detail_url)
            if school.school_status != status:
                school.school_status = status
                uf = ["school_status"]
                if status in (School.SchoolStatus.ACTIVE, School.SchoolStatus.TRIAL) and not school.is_archived:
                    school.is_active = True
                    uf.append("is_active")
                school.save(update_fields=uf)
                messages.success(request, f"Status updated to {school.get_school_status_display()}.")
            else:
                messages.info(request, "Status unchanged.")
            return redirect(detail_url)

        messages.error(request, "Unknown action.")
        return redirect(detail_url)

    from apps.core.platform_financials import _safe_tenant_footprint

    teachers_ct, students_ct, classes_ct = _safe_tenant_footprint(school)
    stu_live, _ = _safe_tenant_counts(school)
    bd = school.saas_billing_monthly_breakdown(stu_live)
    eff_start = _effective_billing_start_date(school)
    today = timezone.localdate()
    in_free_period = bool(eff_start and today < eff_start)

    primary_domain = (
        Domain.objects.filter(tenant=school, is_primary=True).values_list("domain", flat=True).first()
        or Domain.objects.filter(tenant=school).values_list("domain", flat=True).first()
    )

    recent_invoices = list(
        school.generated_invoices.order_by("-created_at")[:12].values(
            "invoice_number",
            "invoice_month_key",
            "grand_total",
            "status",
            "created_at",
        )
    )

    plans = list(Plan.objects.filter(is_active=True).prefetch_related("features"))

    domain_rows = list(
        Domain.objects.filter(tenant=school)
        .order_by("-is_primary", "domain")
        .values("domain", "is_primary")
    )
    billing_plan_label = "—"
    if school.billing_plan_id:
        try:
            billing_plan_label = school.billing_plan.get_name_display()
        except Exception:
            billing_plan_label = str(school.billing_plan_id)
    subscription_plan_label = "—"
    if school.subscription_plan_id:
        sp = school.subscription_plan
        subscription_plan_label = f"{sp.name} ({sp.get_plan_type_display()})"
    platform_control_meta_json = json.dumps(
        school.platform_control_meta or {},
        indent=2,
        ensure_ascii=False,
        default=str,
    )
    feature_addon_rows = list(
        school.feature_addons.select_related("feature")
        .order_by("-is_enabled", "feature__name")
        .values("is_enabled", "extra_monthly_charge", "notes", "feature__name", "feature__code")
    )

    billing_url = reverse("core:super_admin:control_center_section", kwargs={"section": "billing"})
    billing_timeline_url = reverse("core:super_admin:billing_school_detail", kwargs={"school_id": school.pk})
    billing_config_post_url = reverse("core:super_admin:schools_api_update_billing_config")

    # User.school uses to_field="code" — filter by school FK, not numeric pk in school_id column.
    # Role is ADMIN (school admin); prefer first active admin, else first by pk.
    _adm = AccountUser.objects.filter(school=school, role=AccountUser.Roles.ADMIN).order_by("pk")
    admin_username = (
        _adm.filter(is_active=True).values_list("username", flat=True).first()
        or _adm.values_list("username", flat=True).first()
        or "-"
    )

    return render(
        request,
        "super_admin/pages/school_detail.html",
        {
            "school": school,
            "admin_username": admin_username,
            "plan_label": _school_plan_label(school),
            "teachers_ct": teachers_ct,
            "students_ct": students_ct,
            "classes_ct": classes_ct,
            "breakdown": bd,
            "effective_billing_start": eff_start,
            "in_free_period": in_free_period,
            "primary_domain": primary_domain or "",
            "domain_rows": domain_rows,
            "billing_plan_label": billing_plan_label,
            "subscription_plan_label": subscription_plan_label,
            "platform_control_meta_json": platform_control_meta_json,
            "feature_addon_rows": feature_addon_rows,
            "recent_invoices": recent_invoices,
            "plans": plans,
            "schools_list_url": schools_list_url,
            "billing_url": billing_url,
            "billing_timeline_url": billing_timeline_url,
            "billing_config_post_url": billing_config_post_url,
        },
    )


def _parse_money(val) -> Decimal | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return Decimal(str(val))
    s = str(val).strip().replace(",", "")
    if s == "":
        return Decimal("0")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _billing_invoice_overdue_days() -> int:
    from apps.super_admin import settings_service

    return settings_service.billing_auto_mark_overdue_days()


_BILLING_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _month_last_date(y: int, m: int) -> date:
    from calendar import monthrange

    return date(y, m, monthrange(y, m)[1])


def _effective_free_until_date(school: School) -> date | None:
    fu = getattr(school, "saas_free_until_date", None)
    if fu:
        return fu
    return getattr(school, "saas_billing_complimentary_until", None)


def _effective_service_start_date(school: School) -> date | None:
    return getattr(school, "saas_service_start_date", None)


def _effective_billing_start_date(school: School) -> date | None:
    """First billable day: explicit ``billing_start_date``, else day after free-until."""
    if getattr(school, "billing_start_date", None):
        return school.billing_start_date
    fu = _effective_free_until_date(school)
    if fu:
        return fu + timedelta(days=1)
    return None


def _billing_period_starts_before_commencement(
    school: School, by: int, bm: int | None, is_yearly: bool
) -> bool:
    start = _effective_billing_start_date(school)
    if start is None:
        return False
    if is_yearly:
        period_start = date(by, 1, 1)
    else:
        if bm is None or bm < 1 or bm > 12:
            return False
        period_start = date(by, bm, 1)
    return period_start < start


def _generated_invoice_payment_state(inv: SchoolGeneratedInvoice) -> str:
    if inv.status == SchoolGeneratedInvoice.Status.PAID:
        return "paid"
    if inv.status == SchoolGeneratedInvoice.Status.VOID:
        return "void"
    if inv.due_date and timezone.localdate() > inv.due_date:
        return "overdue"
    return "pending"


def _billing_due_date_for_period(year: int, month: int | None) -> date:
    grace = int(getattr(settings, "BILLING_INVOICE_OVERDUE_DAYS", 14))
    if month and 1 <= month <= 12:
        period_end = _month_last_date(year, month)
    else:
        period_end = date(year, 12, 31)
    return period_end + timedelta(days=grace)


def _billing_payload_bool(payload: dict, key: str) -> bool:
    v = payload.get(key)
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _invoice_month_key_to_display(inv_key: str) -> str:
    if not inv_key:
        return ""
    parts = inv_key.strip().split("-")
    if len(parts) < 2:
        return inv_key
    try:
        y = int(parts[0])
        mo = int(parts[1])
    except ValueError:
        return inv_key
    if mo == 0:
        return f"Year {y}"
    if 1 <= mo <= 12:
        return f"{_BILLING_MONTH_NAMES[mo - 1]} {y}"
    return inv_key


def _billing_card_billing_surface(
    school: School, has_pending_invoice: bool, *, schedule_period_free: bool = False
) -> dict[str, str]:
    if schedule_period_free:
        return {"slug": "free_period", "label": "Free Period"}
    waived, _ = _billing_complimentary_waiver_active(school)
    if waived:
        return {"slug": "free_period", "label": "Free Period"}
    if has_pending_invoice:
        return {"slug": "pending", "label": "Pending"}
    return {"slug": "paid_up", "label": "Paid"}


def _invoice_history_row_extras(inv: SchoolGeneratedInvoice) -> dict[str, str]:
    snap = inv.snapshot or {}
    inv_key = (inv.invoice_month_key or "").strip() or (snap.get("invoice_month_key") or "")
    month_display = (snap.get("invoice_period_label") or "").strip() or _invoice_month_key_to_display(inv_key)
    amt = inv.grand_total
    waived_snap = bool(snap.get("complimentary_waiver_applied"))
    if inv.status == SchoolGeneratedInvoice.Status.PAID:
        status_display = "Paid"
    elif inv.status == SchoolGeneratedInvoice.Status.VOID:
        status_display = "Void"
    elif waived_snap or (amt == 0 and inv.status == SchoolGeneratedInvoice.Status.ISSUED):
        status_display = "Free Period"
    else:
        ps = _generated_invoice_payment_state(inv)
        status_display = {"paid": "Paid", "void": "Void", "overdue": "Overdue", "pending": "Pending"}.get(
            ps, ps
        )
    paid_date_display = "—"
    if inv.paid_at:
        pd = timezone.localtime(inv.paid_at).date()
        paid_date_display = f"{_BILLING_MONTH_NAMES[pd.month - 1]} {pd.day}, {pd.year}"
    return {
        "month_display": month_display,
        "status_display": status_display,
        "paid_date_display": paid_date_display,
    }


def _parse_invoice_month_key(inv_key: str) -> tuple[int, int | None]:
    today = timezone.localdate()
    if not inv_key or "-" not in inv_key:
        return today.year, today.month
    parts = inv_key.strip().split("-")
    try:
        y = int(parts[0])
        mo = int(parts[1])
    except (ValueError, IndexError):
        return today.year, today.month
    if mo == 0:
        return y, None
    if 1 <= mo <= 12:
        return y, mo
    return y, today.month


def _billing_shift_calendar_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _billing_mmm_yyyy_period_options(*, past: int = 14, future: int = 18) -> list[dict[str, str]]:
    """Monthly period choices: value ``YYYY-MM``, label ``MMM YYYY``."""
    today = timezone.localdate()
    y, m = today.year, today.month
    start_y, start_m = _billing_shift_calendar_month(y, m, -past)
    out: list[dict[str, str]] = []
    cy, cm = start_y, start_m
    for _ in range(past + future + 1):
        label = f"{_BILLING_MONTH_NAMES[cm - 1]} {cy}"
        val = f"{cy:04d}-{cm:02d}"
        out.append({"value": val, "label": label})
        cy, cm = _billing_shift_calendar_month(cy, cm, 1)
    return out


def _billing_timeline_anchor_ym(school: School) -> tuple[int, int]:
    reg = getattr(school, "registration_date", None)
    if reg:
        return int(reg.year), int(reg.month)
    c = school.created_on
    if timezone.is_naive(c):
        c = timezone.make_aware(c)
    loc = timezone.localtime(c)
    return int(loc.year), int(loc.month)


def _billing_timeline_invoice_for_key(school: School, inv_key: str) -> SchoolGeneratedInvoice | None:
    return (
        school.generated_invoices.filter(invoice_month_key=inv_key)
        .exclude(status=SchoolGeneratedInvoice.Status.VOID)
        .order_by("-created_at")
        .first()
    )


def _billing_month_end_in_complimentary(year: int, month: int, comp_until: date) -> bool:
    return _month_last_date(year, month) <= comp_until


def _billing_build_saas_timeline_rows(school: School, stu_live: int) -> list[dict]:
    """
    Calendar timeline from registration month through current month (or year rows if yearly).
    Free vs billable uses last day of month vs effective billing start (same as invoice commence).
    """
    today = timezone.localdate()
    eff = _effective_billing_start_date(school)
    waived, comp_until = _billing_complimentary_waiver_active(school)
    bd = school.saas_billing_monthly_breakdown(stu_live)
    is_yearly = school.saas_billing_cycle == School.SaaSBillingCycle.YEARLY
    q2 = Decimal("0.01")
    if is_yearly:
        preview_total = Decimal(str(bd.get("final_period", "0"))).quantize(q2)
        preview_base = (Decimal(str(bd.get("base_cost", "0"))) * Decimal("12")).quantize(q2)
        preview_extra = (Decimal(str(bd.get("extra_cost", "0"))) * Decimal("12")).quantize(q2)
        preview_conc = (Decimal(str(bd.get("concession_cost", "0"))) * Decimal("12")).quantize(q2)
    else:
        preview_total = Decimal(str(bd.get("final_monthly", "0"))).quantize(q2)
        preview_base = Decimal(str(bd.get("base_cost", "0"))).quantize(q2)
        preview_extra = Decimal(str(bd.get("extra_cost", "0"))).quantize(q2)
        preview_conc = Decimal(str(bd.get("concession_cost", "0"))).quantize(q2)

    rows: list[dict] = []
    MAX_STEPS = 72
    steps = 0

    if is_yearly:
        sy, _ = _billing_timeline_anchor_ym(school)
        for y in range(sy, today.year + 1):
            if steps >= MAX_STEPS:
                break
            steps += 1
            inv_key = f"{y:04d}-00"
            period_start = date(y, 1, 1)
            before_billing = eff is not None and period_start < eff
            waived_row = bool(
                waived
                and comp_until
                and date(y, 12, 31) <= comp_until
                and not before_billing
            )
            inv = _billing_timeline_invoice_for_key(school, inv_key)
            row = _billing_timeline_row_dict(
                year=y,
                month=None,
                label=f"Year {y}",
                inv_key=inv_key,
                today=today,
                before_billing=before_billing,
                waived_row=waived_row,
                inv=inv,
                preview_total=preview_total,
                preview_base=preview_base,
                preview_extra=preview_extra,
                preview_conc=preview_conc,
                is_yearly=True,
            )
            rows.append(row)
        return rows

    sy, sm = _billing_timeline_anchor_ym(school)
    ey, em = today.year, today.month
    cy, cm = sy, sm
    while steps < MAX_STEPS:
        if cy > ey or (cy == ey and cm > em):
            break
        inv_key = f"{cy:04d}-{cm:02d}"
        month_end = _month_last_date(cy, cm)
        before_billing = eff is not None and month_end < eff
        waived_row = bool(
            waived and comp_until and _billing_month_end_in_complimentary(cy, cm, comp_until) and not before_billing
        )
        inv = _billing_timeline_invoice_for_key(school, inv_key)
        label = f"{_BILLING_MONTH_NAMES[cm - 1][:3]} {cy}"
        rows.append(
            _billing_timeline_row_dict(
                year=cy,
                month=cm,
                label=label,
                inv_key=inv_key,
                today=today,
                before_billing=before_billing,
                waived_row=waived_row,
                inv=inv,
                preview_total=preview_total,
                preview_base=preview_base,
                preview_extra=preview_extra,
                preview_conc=preview_conc,
                is_yearly=False,
            )
        )
        cy, cm = _billing_shift_calendar_month(cy, cm, 1)
        steps += 1
    return rows


def _billing_timeline_row_dict(
    *,
    year: int,
    month: int | None,
    label: str,
    inv_key: str,
    today: date,
    before_billing: bool,
    waived_row: bool,
    inv: SchoolGeneratedInvoice | None,
    preview_total: Decimal,
    preview_base: Decimal,
    preview_extra: Decimal,
    preview_conc: Decimal,
    is_yearly: bool,
) -> dict:
    is_current = (year == today.year and month == today.month) if month else (year == today.year and is_yearly)
    is_complimentary = waived_row and not before_billing
    is_any_free = before_billing or is_complimentary

    amount_display = "₹0"
    amount_value = Decimal("0")
    status_slug = "free_period"
    status_label = "Free Period"
    invoice_id = None
    invoice_number = ""
    paid_at_display = "—"
    due_date_iso = ""

    if inv:
        invoice_id = inv.pk
        invoice_number = inv.invoice_number or ""
        amount_value = inv.grand_total
        amount_display = f"₹{inv.grand_total}"
        if inv.status == SchoolGeneratedInvoice.Status.PAID:
            status_slug = "paid"
            status_label = "Paid"
            if inv.paid_at:
                pd = timezone.localtime(inv.paid_at).date()
                paid_at_display = f"{_BILLING_MONTH_NAMES[pd.month - 1]} {pd.day}, {pd.year}"
        elif inv.status == SchoolGeneratedInvoice.Status.ISSUED:
            snap = inv.snapshot or {}
            if snap.get("complimentary_waiver_applied") or (
                inv.grand_total == 0 and inv.status == SchoolGeneratedInvoice.Status.ISSUED
            ):
                status_slug = "free_period"
                status_label = "Free Period"
            else:
                ps = _generated_invoice_payment_state(inv)
                status_slug = ps if ps in ("pending", "overdue") else "pending"
                status_label = status_slug.title()
        if inv.due_date:
            due_date_iso = inv.due_date.isoformat()
    elif not is_any_free:
        status_slug = "pending"
        status_label = "Pending"
        amount_value = preview_total
        amount_display = f"₹{preview_total}"

    is_billable = not before_billing and not is_complimentary

    return {
        "year": year,
        "month": month,
        "label": label,
        "invoice_month_key": inv_key,
        "is_yearly_row": is_yearly,
        "is_current": is_current,
        "is_free_period": before_billing,
        "is_complimentary": is_complimentary,
        "is_any_free": is_any_free,
        "is_billable": is_billable,
        "status_slug": status_slug,
        "status_label": status_label,
        "amount_display": amount_display,
        "amount_value": amount_value,
        "preview_total": preview_total,
        "preview_base": preview_base,
        "preview_extra": preview_extra,
        "preview_conc": preview_conc,
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "paid_at_display": paid_at_display,
        "due_date_iso": due_date_iso,
        "invoice_gst": str(inv.gst_amount) if inv else "",
        "invoice_subtotal": str(inv.subtotal_before_gst) if inv else "",
        "invoice_base": str(inv.base_amount) if inv else "",
        "invoice_extra": str(inv.extra_amount) if inv else "",
        "invoice_concession": str(inv.concession_amount) if inv else "",
        "invoice_include_gst": bool(inv.include_gst) if inv else False,
    }


def _billing_school_money_totals(school: School) -> dict[str, Decimal]:
    voided = SchoolGeneratedInvoice.Status.VOID
    base = school.generated_invoices.exclude(status=voided)
    outstanding = base.filter(status=SchoolGeneratedInvoice.Status.ISSUED).aggregate(
        s=Sum("grand_total")
    )["s"] or Decimal("0")
    total_paid = base.filter(status=SchoolGeneratedInvoice.Status.PAID).aggregate(s=Sum("grand_total"))[
        "s"
    ] or Decimal("0")
    total_billed = base.aggregate(s=Sum("grand_total"))["s"] or Decimal("0")
    return {
        "outstanding": outstanding.quantize(Decimal("0.01")),
        "total_paid": total_paid.quantize(Decimal("0.01")),
        "total_billed": total_billed.quantize(Decimal("0.01")),
    }


def _invoice_badge_status(inv: SchoolGeneratedInvoice) -> str:
    """UX badge: free | paid | pending | overdue | void."""
    if inv.status == SchoolGeneratedInvoice.Status.VOID:
        return "void"
    if inv.status == SchoolGeneratedInvoice.Status.PAID:
        return "paid"
    snap = inv.snapshot or {}
    waived_snap = bool(snap.get("complimentary_waiver_applied"))
    if waived_snap or (inv.grand_total == 0 and inv.status == SchoolGeneratedInvoice.Status.ISSUED):
        return "free"
    if inv.status == SchoolGeneratedInvoice.Status.ISSUED and inv.due_date:
        if timezone.localdate() > inv.due_date:
            return "overdue"
    return "pending"


def _billing_school_dates_public(school: School) -> dict:
    """Canonical + alias field names for API consumers."""
    eff = _effective_billing_start_date(school)
    return {
        "id": school.pk,
        "name": school.name,
        "code": school.code,
        "service_start_date": (
            school.saas_service_start_date.isoformat() if school.saas_service_start_date else None
        ),
        "free_until_date": school.saas_free_until_date.isoformat() if school.saas_free_until_date else None,
        "registration_date": (
            school.registration_date.isoformat() if getattr(school, "registration_date", None) else None
        ),
        "billing_start_date": (
            school.billing_start_date.isoformat() if getattr(school, "billing_start_date", None) else None
        ),
        "effective_billing_start_date": eff.isoformat() if eff else None,
        "saas_service_start_date": (
            school.saas_service_start_date.isoformat() if school.saas_service_start_date else None
        ),
        "saas_free_until_date": school.saas_free_until_date.isoformat() if school.saas_free_until_date else None,
        "saas_billing_cycle": school.saas_billing_cycle,
        "saas_billing_auto_renew": school.saas_billing_auto_renew,
    }


def _invoice_public_api_dict(inv: SchoolGeneratedInvoice) -> dict:
    """Normalized SaaS invoice row for REST list (maps DB fields to requested names)."""
    hx = _invoice_history_row_extras(inv)
    ps = _generated_invoice_payment_state(inv)
    badge = _invoice_badge_status(inv)
    gen_dt = timezone.localtime(inv.created_at).date()
    paid_dt = timezone.localtime(inv.paid_at).date() if inv.paid_at else None
    return {
        "id": inv.pk,
        "school_id": inv.school_id,
        "invoice_number": inv.invoice_number,
        "invoice_month": inv.invoice_month_key,
        "invoice_month_key": inv.invoice_month_key,
        "base_amount": str(inv.base_amount),
        "extra_amount": str(inv.extra_amount),
        "concession_amount": str(inv.concession_amount),
        "final_amount": str(inv.grand_total),
        "grand_total": str(inv.grand_total),
        "subtotal_before_gst": str(inv.subtotal_before_gst),
        "gst_amount": str(inv.gst_amount),
        "include_gst": inv.include_gst,
        "status": inv.status,
        "payment_state": ps,
        "badge_status": badge,
        "generated_date": gen_dt.isoformat(),
        "paid_date": paid_dt.isoformat() if paid_dt else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "month_display": hx["month_display"],
        "status_display": hx["status_display"],
        "paid_date_display": hx["paid_date_display"],
        "automation_source": (inv.snapshot or {}).get("automation_source"),
    }


def _billing_invoice_period_payload(
    school: School,
    *,
    year: int | None = None,
    month: int | None = None,
) -> dict:
    """Human-readable period for an invoice (defaults to current local month/year)."""
    today = timezone.localdate()
    y = int(year) if year is not None else today.year
    if school.saas_billing_cycle == School.SaaSBillingCycle.YEARLY:
        label = f"Calendar year {y}"
        return {
            "kind": "yearly",
            "label": label,
            "year": y,
            "month": None,
            "intro_sentence": f"This invoice is for annual billing for {label}.",
        }
    m = int(month) if month is not None else today.month
    m = max(1, min(12, m))
    label = f"{_BILLING_MONTH_NAMES[m - 1]} {y}"
    return {
        "kind": "monthly",
        "label": label,
        "year": y,
        "month": m,
        "intro_sentence": f"This invoice is for the month of {label}.",
    }


def _billing_complimentary_waiver_active(school: School) -> tuple[bool, date | None]:
    until = _effective_free_until_date(school)
    if until is None:
        return False, None
    today = timezone.localdate()
    if today <= until:
        return True, until
    return False, None


def _billing_last_payment_dates_bulk(school_ids: list[int]) -> dict[int, date]:
    acc: dict[int, date] = {}
    if not school_ids:
        return acc
    for row in SaaSPlatformPayment.objects.filter(school_id__in=school_ids).values("school_id").annotate(
        m=Max("payment_date")
    ):
        sid, d = row["school_id"], row["m"]
        if d:
            prev = acc.get(sid)
            acc[sid] = d if prev is None else max(prev, d)
    for row in (
        SchoolGeneratedInvoice.objects.filter(
            school_id__in=school_ids,
            status=SchoolGeneratedInvoice.Status.PAID,
        )
        .exclude(paid_at__isnull=True)
        .values("school_id")
        .annotate(m=Max("paid_at"))
    ):
        sid, dt = row["school_id"], row["m"]
        if not dt:
            continue
        d = dt.date() if hasattr(dt, "date") else dt
        prev = acc.get(sid)
        acc[sid] = d if prev is None else max(prev, d)
    for row in PlatformInvoicePayment.objects.filter(school_id__in=school_ids).values("school_id").annotate(
        m=Max("paid_on")
    ):
        sid, dt = row["school_id"], row["m"]
        if not dt:
            continue
        d = dt.date() if hasattr(dt, "date") else dt
        prev = acc.get(sid)
        acc[sid] = d if prev is None else max(prev, d)
    return acc


def _billing_oldest_issued_invoice_bulk(school_ids: list[int]) -> dict[int, datetime]:
    out: dict[int, datetime] = {}
    if not school_ids:
        return out
    for row in SchoolGeneratedInvoice.objects.filter(
        school_id__in=school_ids,
        status=SchoolGeneratedInvoice.Status.ISSUED,
    ).values("school_id").annotate(oldest=Min("created_at")):
        if row["oldest"]:
            out[row["school_id"]] = row["oldest"]
    return out


def _billing_nested_billing_dict(
    school: School,
    bd: dict,
    tenant_students: int,
    *,
    last_payment: date | None,
    pending_overdue: bool,
) -> dict:
    if school.plan_id:
        plan_name = (
            "Premium · Enterprise"
            if school.plan.name == PlanName.PREMIUM
            else school.plan.get_name_display()
        )
    else:
        plan_name = "No plan"
    waived, _ = _billing_complimentary_waiver_active(school)
    fu = _effective_free_until_date(school)
    ss = _effective_service_start_date(school)
    eff_commence = _effective_billing_start_date(school)
    return {
        "school_id": school.pk,
        "school_name": school.name,
        "plan_name": plan_name,
        "price_per_student": bd["plan_price_per_student"],
        "total_students": int(tenant_students),
        "extra_per_student": bd["billing_extra_per_student_month"],
        "concession_per_student": bd["billing_concession_per_student_month"],
        "billing_cycle": school.saas_billing_cycle,
        "status": school.school_status,
        "last_payment_date": last_payment.isoformat() if last_payment else None,
        "pending_payment_overdue": pending_overdue,
        "saas_billing_auto_renew": school.saas_billing_auto_renew,
        "saas_service_start_date": ss.isoformat() if ss else None,
        "saas_free_until_date": fu.isoformat() if fu else None,
        "saas_billing_complimentary_until": (
            school.saas_billing_complimentary_until.isoformat()
            if school.saas_billing_complimentary_until
            else None
        ),
        "registration_date": (
            school.registration_date.isoformat() if getattr(school, "registration_date", None) else None
        ),
        "billing_start_date": (
            school.billing_start_date.isoformat() if getattr(school, "billing_start_date", None) else None
        ),
        "effective_billing_start_date": eff_commence.isoformat() if eff_commence else None,
        "complimentary_waiver_active": waived,
    }


def _billing_apply_terms_patch(school: School, user, payload: dict) -> JsonResponse:
    raw_keys = set(payload.keys())
    if "free_until_date" in payload and "saas_free_until_date" not in payload:
        payload = {**payload, "saas_free_until_date": payload.get("free_until_date")}
    requested_free_change = bool(
        raw_keys & {"saas_free_until_date", "saas_billing_complimentary_until", "free_until_date"}
    )

    before_terms = {
        "billing_extra_per_student_month": str(school.billing_extra_per_student_month),
        "billing_concession_per_student_month": str(school.billing_concession_per_student_month),
        "saas_billing_cycle": school.saas_billing_cycle,
        "saas_billing_auto_renew": school.saas_billing_auto_renew,
        "saas_billing_complimentary_until": (
            school.saas_billing_complimentary_until.isoformat()
            if school.saas_billing_complimentary_until
            else None
        ),
        "saas_service_start_date": (
            school.saas_service_start_date.isoformat() if school.saas_service_start_date else None
        ),
        "saas_free_until_date": school.saas_free_until_date.isoformat() if school.saas_free_until_date else None,
        "registration_date": (
            school.registration_date.isoformat() if getattr(school, "registration_date", None) else None
        ),
        "billing_start_date": (
            school.billing_start_date.isoformat() if getattr(school, "billing_start_date", None) else None
        ),
    }

    update_fields: list[str] = []
    explicit_billing_start = "billing_start_date" in payload

    def _parse_saas_date_field(raw, field_label: str) -> date | None | JsonResponse:
        if raw in (None, "", False, "null", "None"):
            return None
        if not isinstance(raw, str):
            return JsonResponse({"ok": False, "error": f"Invalid {field_label}"}, status=400)
        s = raw.strip()[:10]
        try:
            y_str, mo_str, d_str = s.split("-", 2)
            parsed = date(int(y_str), int(mo_str), int(d_str))
        except (ValueError, TypeError):
            return JsonResponse({"ok": False, "error": f"Invalid {field_label}"}, status=400)
        today = timezone.localdate()
        if parsed < today - timedelta(days=3650) or parsed > today + timedelta(days=3650):
            return JsonResponse({"ok": False, "error": f"{field_label} out of allowed range"}, status=400)
        return parsed

    if "billing_extra_per_student_month" in payload:
        d = _parse_money(payload.get("billing_extra_per_student_month"))
        if d is None or d < 0 or d > Decimal("999999.99"):
            return JsonResponse({"ok": False, "error": "Invalid billing_extra_per_student_month"}, status=400)
        school.billing_extra_per_student_month = d
        update_fields.append("billing_extra_per_student_month")

    if "billing_concession_per_student_month" in payload:
        d = _parse_money(payload.get("billing_concession_per_student_month"))
        if d is None or d < 0 or d > Decimal("999999.99"):
            return JsonResponse({"ok": False, "error": "Invalid billing_concession_per_student_month"}, status=400)
        school.billing_concession_per_student_month = d
        update_fields.append("billing_concession_per_student_month")

    if "saas_billing_cycle" in payload:
        cycle = (payload.get("saas_billing_cycle") or "").strip().lower()
        allowed = {c[0] for c in School.SaaSBillingCycle.choices}
        if cycle not in allowed:
            return JsonResponse({"ok": False, "error": "Invalid saas_billing_cycle"}, status=400)
        school.saas_billing_cycle = cycle
        update_fields.append("saas_billing_cycle")

    if "saas_billing_auto_renew" in payload:
        v = payload.get("saas_billing_auto_renew")
        if isinstance(v, str):
            school.saas_billing_auto_renew = v.strip().lower() in ("1", "true", "yes", "on")
        else:
            school.saas_billing_auto_renew = bool(v)
        update_fields.append("saas_billing_auto_renew")

    if "saas_billing_complimentary_until" in payload:
        raw = payload.get("saas_billing_complimentary_until")
        if raw in (None, "", False, "null", "None"):
            school.saas_billing_complimentary_until = None
            school.saas_free_until_date = None
            update_fields.extend(["saas_billing_complimentary_until", "saas_free_until_date"])
        else:
            parsed = _parse_saas_date_field(raw, "saas_billing_complimentary_until")
            if isinstance(parsed, JsonResponse):
                return parsed
            school.saas_billing_complimentary_until = parsed
            school.saas_free_until_date = parsed
            update_fields.extend(["saas_billing_complimentary_until", "saas_free_until_date"])

    if "saas_service_start_date" in payload:
        raw = payload.get("saas_service_start_date")
        if raw in (None, "", False, "null", "None"):
            school.saas_service_start_date = None
            update_fields.append("saas_service_start_date")
        else:
            parsed = _parse_saas_date_field(raw, "saas_service_start_date")
            if isinstance(parsed, JsonResponse):
                return parsed
            school.saas_service_start_date = parsed
            update_fields.append("saas_service_start_date")

    if "saas_free_until_date" in payload:
        raw = payload.get("saas_free_until_date")
        if raw in (None, "", False, "null", "None"):
            school.saas_free_until_date = None
            school.saas_billing_complimentary_until = None
            update_fields.extend(["saas_free_until_date", "saas_billing_complimentary_until"])
        else:
            parsed = _parse_saas_date_field(raw, "saas_free_until_date")
            if isinstance(parsed, JsonResponse):
                return parsed
            school.saas_free_until_date = parsed
            school.saas_billing_complimentary_until = parsed
            update_fields.extend(["saas_free_until_date", "saas_billing_complimentary_until"])

    if "registration_date" in payload:
        raw = payload.get("registration_date")
        if raw in (None, "", False, "null", "None"):
            school.registration_date = None
            update_fields.append("registration_date")
        else:
            parsed = _parse_saas_date_field(raw, "registration_date")
            if isinstance(parsed, JsonResponse):
                return parsed
            school.registration_date = parsed
            update_fields.append("registration_date")

    if "billing_start_date" in payload:
        raw = payload.get("billing_start_date")
        if raw in (None, "", False, "null", "None"):
            school.billing_start_date = None
            update_fields.append("billing_start_date")
        else:
            parsed = _parse_saas_date_field(raw, "billing_start_date")
            if isinstance(parsed, JsonResponse):
                return parsed
            school.billing_start_date = parsed
            update_fields.append("billing_start_date")

    if requested_free_change and not explicit_billing_start:
        if school.saas_free_until_date:
            school.billing_start_date = school.saas_free_until_date + timedelta(days=1)
        else:
            school.billing_start_date = None
        if "billing_start_date" not in update_fields:
            update_fields.append("billing_start_date")

    reg = getattr(school, "registration_date", None)
    bs = getattr(school, "billing_start_date", None)
    if reg and bs and bs < reg:
        return JsonResponse(
            {"ok": False, "error": "billing_start_date cannot be before registration_date"},
            status=400,
        )

    if update_fields:
        plan_price = Decimal(school.plan.price) if school.plan_id else Decimal("0")
        extra_ps = Decimal(school.billing_extra_per_student_month or 0)
        cap = (plan_price + extra_ps).quantize(Decimal("0.01"))
        conc = Decimal(school.billing_concession_per_student_month or 0)
        if conc > cap:
            school.billing_concession_per_student_month = cap
            if "billing_concession_per_student_month" not in update_fields:
                update_fields.append("billing_concession_per_student_month")

    if not update_fields:
        return JsonResponse({"ok": False, "error": "No valid fields to update"}, status=400)

    update_fields = list(dict.fromkeys(update_fields))
    school.save(update_fields=update_fields)
    school.refresh_from_db(fields=update_fields)

    after_terms = {
        "billing_extra_per_student_month": str(school.billing_extra_per_student_month),
        "billing_concession_per_student_month": str(school.billing_concession_per_student_month),
        "saas_billing_cycle": school.saas_billing_cycle,
        "saas_billing_auto_renew": school.saas_billing_auto_renew,
        "saas_billing_complimentary_until": (
            school.saas_billing_complimentary_until.isoformat()
            if school.saas_billing_complimentary_until
            else None
        ),
        "saas_service_start_date": (
            school.saas_service_start_date.isoformat() if school.saas_service_start_date else None
        ),
        "saas_free_until_date": school.saas_free_until_date.isoformat() if school.saas_free_until_date else None,
        "registration_date": (
            school.registration_date.isoformat() if getattr(school, "registration_date", None) else None
        ),
        "billing_start_date": (
            school.billing_start_date.isoformat() if getattr(school, "billing_start_date", None) else None
        ),
    }
    SchoolBillingAuditLog.objects.create(
        school=school,
        kind=SchoolBillingAuditLog.Kind.BILLING_TERMS,
        summary="Updated SaaS billing terms",
        payload={"before": before_terms, "after": after_terms},
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )

    stu, _ = _safe_tenant_counts(school)
    bd = school.saas_billing_monthly_breakdown(stu)
    last_map = _billing_last_payment_dates_bulk([school.pk])
    oldest_map = _billing_oldest_issued_invoice_bulk([school.pk])
    overdue_cut = timezone.now() - timedelta(days=_billing_invoice_overdue_days())
    oldest = oldest_map.get(school.pk)
    pending_over = bool(oldest and oldest < overdue_cut)
    bill = _billing_nested_billing_dict(
        school,
        bd,
        stu,
        last_payment=last_map.get(school.pk),
        pending_overdue=pending_over,
    )
    eff_bs = _effective_billing_start_date(school)
    return JsonResponse(
        {
            "ok": True,
            "school_id": school.pk,
            "breakdown": bd,
            "billing": bill,
            "billing_extra_per_student_month": str(school.billing_extra_per_student_month),
            "billing_concession_per_student_month": str(school.billing_concession_per_student_month),
            "saas_billing_cycle": school.saas_billing_cycle,
            "saas_billing_auto_renew": school.saas_billing_auto_renew,
            "saas_billing_complimentary_until": (
                school.saas_billing_complimentary_until.isoformat()
                if school.saas_billing_complimentary_until
                else None
            ),
            "saas_service_start_date": (
                school.saas_service_start_date.isoformat() if school.saas_service_start_date else None
            ),
            "saas_free_until_date": school.saas_free_until_date.isoformat() if school.saas_free_until_date else None,
            "registration_date": (
                school.registration_date.isoformat() if getattr(school, "registration_date", None) else None
            ),
            "billing_start_date": (
                school.billing_start_date.isoformat() if getattr(school, "billing_start_date", None) else None
            ),
            "effective_billing_start_date": eff_bs.isoformat() if eff_bs else None,
        }
    )


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def billing_school_update_api(request, school_id: int):
    """
    JSON body (only keys present are updated):
      { "billing_extra_per_student_month": "10.00",
        "billing_concession_per_student_month": "5",
        "saas_billing_cycle": "monthly" | "yearly",
        "saas_billing_auto_renew": true,
        "saas_billing_complimentary_until": "2026-04-30" | null,
        "saas_service_start_date": "2026-01-01" | null,
        "saas_free_until_date": "2026-03-31" | null }
    Concession is capped so per-student net is never negative; finals are never negative.
    """
    connection.set_schema_to_public()
    school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "error": "Body must be a JSON object"}, status=400)

    return _billing_apply_terms_patch(school, request.user, payload)


def _next_school_generated_invoice_number() -> str:
    d = timezone.now().date()
    prefix = f"INV-{d.strftime('%Y%m%d')}"
    n = SchoolGeneratedInvoice.objects.filter(invoice_number__startswith=prefix).count() + 1
    return f"{prefix}-{n:06d}"


def _billing_log(school: School, kind: str, summary: str, payload: dict, user) -> None:
    SchoolBillingAuditLog.objects.create(
        school=school,
        kind=kind,
        summary=summary[:512],
        payload=payload or {},
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )


def _billing_generate_invoice_response(school: School, user, payload: dict) -> JsonResponse:
    include_gst = bool(payload.get("include_gst"))
    gst_rate = _parse_money(payload.get("gst_rate_percent"))
    if gst_rate is None:
        gst_rate = Decimal("18")
    if gst_rate < 0 or gst_rate > 100:
        return JsonResponse({"ok": False, "error": "Invalid gst_rate_percent"}, status=400)

    today = timezone.localdate()
    is_yearly = school.saas_billing_cycle == School.SaaSBillingCycle.YEARLY
    try:
        raw_y = payload.get("billing_year")
        by = int(raw_y) if raw_y not in (None, "") else today.year
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid billing_year"}, status=400)
    if by < 2000 or by > today.year + 6:
        return JsonResponse({"ok": False, "error": "billing_year out of range"}, status=400)

    if is_yearly:
        bm: int | None = None
        inv_key = f"{by:04d}-00"
        period_end = date(by, 12, 31)
    else:
        try:
            raw_m = payload.get("billing_month")
            bm = int(raw_m) if raw_m not in (None, "") else today.month
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Invalid billing_month"}, status=400)
        if bm < 1 or bm > 12:
            return JsonResponse({"ok": False, "error": "billing_month must be 1–12"}, status=400)
        inv_key = f"{by:04d}-{bm:02d}"
        period_end = _month_last_date(by, bm)

    ign_free = _billing_payload_bool(payload, "ignore_free_service_window")
    ign_svc = _billing_payload_bool(payload, "ignore_service_start_window")
    free_until = _effective_free_until_date(school)
    if free_until and not ign_free and period_end <= free_until:
        return JsonResponse(
            {
                "ok": False,
                "error": "That billing period is still within the school's free service window. "
                "Use ignore_free_service_window=true only if you intentionally need a delayed/manual record.",
            },
            status=400,
        )
    svc = _effective_service_start_date(school)
    if svc and not ign_svc:
        anchor = date(svc.year, svc.month, 1)
        period_start = date(by, 1, 1) if is_yearly else date(by, bm, 1)
        if period_start < anchor:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "That billing period is before the school's service start date. "
                    "Use ignore_service_start_window=true to override.",
                },
                status=400,
            )

    ign_commence = _billing_payload_bool(payload, "ignore_billing_start_window")
    if not ign_commence and _billing_period_starts_before_commencement(school, by, bm, is_yearly):
        return JsonResponse(
            {
                "ok": False,
                "error": "That period begins before the school's billing start date. "
                "Update dates under Schools → Billing configuration, or use ignore_billing_start_window=true "
                "only for an intentional exception.",
            },
            status=400,
        )

    if _billing_payload_bool(payload, "replace_issued_invoice"):
        for old in SchoolGeneratedInvoice.objects.filter(
            school=school,
            invoice_month_key=inv_key,
            status=SchoolGeneratedInvoice.Status.ISSUED,
        ):
            old.status = SchoolGeneratedInvoice.Status.VOID
            old.save(update_fields=["status"])
            _billing_log(
                school,
                SchoolBillingAuditLog.Kind.INVOICE,
                f"Voided invoice {old.invoice_number} ({inv_key}) for replacement",
                {
                    "invoice_id": old.pk,
                    "invoice_number": old.invoice_number,
                    "reason": "replace_issued_invoice",
                },
                user,
            )

    if SchoolGeneratedInvoice.objects.filter(
        school=school,
        invoice_month_key=inv_key,
        status=SchoolGeneratedInvoice.Status.ISSUED,
    ).exists():
        return JsonResponse(
            {
                "ok": False,
                "error": f"An open (issued) invoice already exists for period {inv_key}. Mark it paid or void it first.",
            },
            status=400,
        )

    stu_live, _ = _safe_tenant_counts(school)
    bd = school.saas_billing_monthly_breakdown(stu_live)
    subtotal = (
        Decimal(bd["final_period"])
        if school.saas_billing_cycle == School.SaaSBillingCycle.YEARLY
        else Decimal(bd["final_monthly"])
    )
    if subtotal < 0:
        subtotal = Decimal("0")
    gst_amount = (
        (subtotal * (gst_rate / Decimal("100"))).quantize(Decimal("0.01")) if include_gst else Decimal("0")
    )
    grand = (subtotal + gst_amount).quantize(Decimal("0.01"))

    waived, waive_until = _billing_complimentary_waiver_active(school)
    nominal_subtotal = subtotal
    nominal_gst = gst_amount
    nominal_grand = grand
    base_amt = Decimal(bd["base_cost"])
    extra_amt = Decimal(bd["extra_cost"])
    conc_amt = Decimal(bd["concession_cost"])
    if waived:
        subtotal = Decimal("0")
        gst_amount = Decimal("0")
        grand = Decimal("0")
        base_amt = extra_amt = conc_amt = Decimal("0")

    manual_applied = False
    manual_grand = _parse_money(payload.get("manual_grand_total"))
    if not waived and manual_grand is not None:
        if manual_grand < 0:
            return JsonResponse({"ok": False, "error": "manual_grand_total must be >= 0"}, status=400)
        grand = manual_grand.quantize(Decimal("0.01"))
        if include_gst and gst_rate > 0:
            subtotal = (grand / (Decimal("1") + gst_rate / Decimal("100"))).quantize(Decimal("0.01"))
            gst_amount = (grand - subtotal).quantize(Decimal("0.01"))
        else:
            subtotal = grand
            gst_amount = Decimal("0")
        manual_applied = True

    plan_label = ""
    plan_ps = Decimal("0")
    if school.plan_id:
        plan_label = (
            "Premium · Enterprise"
            if school.plan.name == PlanName.PREMIUM
            else school.plan.get_name_display()
        )
        plan_ps = Decimal(school.plan.price)

    period = _billing_invoice_period_payload(school, year=by, month=bm)
    due_dt = _billing_due_date_for_period(by, bm)
    snap = {
        "school_name": school.name,
        "school_code": school.code,
        "plan_details": plan_label or "—",
        "plan_price_per_student": bd["plan_price_per_student"],
        "student_count": bd["student_count"],
        "tenant_student_count": bd["tenant_student_count"],
        "uses_student_override": bd["uses_student_override"],
        "base_cost": bd["base_cost"],
        "extra_charges": bd["extra_cost"],
        "discount_concession_label": "Discount / Concession Applied",
        "concession_amount": bd["concession_cost"],
        "subtotal_before_gst": str(subtotal),
        "include_gst": include_gst,
        "gst_rate_percent": str(gst_rate),
        "gst_amount": str(gst_amount),
        "grand_total": str(grand),
        "billing_cycle": school.saas_billing_cycle,
        "invoice_month_key": inv_key,
        "due_date": due_dt.isoformat(),
        "invoice_period_kind": period["kind"],
        "invoice_period_label": period["label"],
        "invoice_period_year": period["year"],
        "invoice_period_month": period["month"],
        "invoice_period_intro": period["intro_sentence"],
        "complimentary_waiver_applied": waived,
        "complimentary_through": waive_until.isoformat() if waive_until else None,
        "nominal_subtotal_before_gst": str(nominal_subtotal),
        "nominal_gst_amount": str(nominal_gst),
        "nominal_grand_total": str(nominal_grand),
        "ignore_free_service_window": ign_free,
        "ignore_service_start_window": ign_svc,
    }
    if _billing_payload_bool(payload, "automation_scheduled"):
        snap["automation_source"] = "scheduled"
    if manual_applied and manual_grand is not None:
        snap["manual_grand_total_applied"] = True
        snap["manual_grand_total"] = str(manual_grand)

    inv = SchoolGeneratedInvoice.objects.create(
        school=school,
        invoice_number=_next_school_generated_invoice_number(),
        status=SchoolGeneratedInvoice.Status.ISSUED,
        include_gst=include_gst,
        gst_rate_percent=gst_rate,
        student_count=int(bd["student_count"]),
        tenant_student_count=int(bd["tenant_student_count"]),
        plan_label=plan_label,
        plan_price_per_student=plan_ps,
        base_amount=base_amt,
        extra_amount=extra_amt,
        concession_amount=conc_amt,
        subtotal_before_gst=subtotal,
        gst_amount=gst_amount,
        grand_total=grand,
        snapshot=snap,
        billing_period_year=by,
        billing_period_month=bm,
        invoice_month_key=inv_key,
        due_date=due_dt,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    log_summary = f"Issued invoice {inv.invoice_number} ({inv_key})"
    if waived:
        log_summary = f"Issued complimentary invoice {inv.invoice_number} (through {waive_until})"
    _billing_log(
        school,
        SchoolBillingAuditLog.Kind.INVOICE,
        log_summary,
        {
            "invoice_id": inv.pk,
            "invoice_number": inv.invoice_number,
            "grand_total": str(grand),
            "waived": waived,
            "invoice_month_key": inv_key,
        },
        user,
    )
    return JsonResponse(
        {
            "ok": True,
            "invoice": {
                "id": inv.pk,
                "invoice_number": inv.invoice_number,
                "grand_total": str(grand),
                "invoice_month_key": inv_key,
                "due_date": due_dt.isoformat(),
                "snapshot": snap,
            },
        }
    )


def _billing_update_generated_invoice_response(school: School, user, payload: dict) -> JsonResponse:
    try:
        inv_id = int(payload.get("invoice_id"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "invoice_id is required"}, status=400)
    inv = school.generated_invoices.filter(pk=inv_id).first()
    if inv is None:
        return JsonResponse({"ok": False, "error": "Invoice not found"}, status=404)
    if inv.status != SchoolGeneratedInvoice.Status.ISSUED:
        return JsonResponse({"ok": False, "error": "Only issued invoices can be updated"}, status=400)

    snap = dict(inv.snapshot or {})
    touched = False
    wants_period = "billing_year" in payload or "billing_month" in payload
    if wants_period:
        today = timezone.localdate()
        is_yearly = school.saas_billing_cycle == School.SaaSBillingCycle.YEARLY
        raw_y = payload["billing_year"] if "billing_year" in payload else inv.billing_period_year
        try:
            by = int(raw_y) if raw_y not in (None, "") else (inv.billing_period_year or today.year)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Invalid billing_year"}, status=400)
        if by < 2000 or by > today.year + 6:
            return JsonResponse({"ok": False, "error": "billing_year out of range"}, status=400)
        if is_yearly:
            bm = None
            new_key = f"{by:04d}-00"
        else:
            raw_m = payload["billing_month"] if "billing_month" in payload else inv.billing_period_month
            try:
                bm = int(raw_m) if raw_m not in (None, "") else (inv.billing_period_month or today.month)
            except (TypeError, ValueError):
                return JsonResponse({"ok": False, "error": "Invalid billing_month"}, status=400)
            if bm < 1 or bm > 12:
                return JsonResponse({"ok": False, "error": "billing_month must be 1–12"}, status=400)
            new_key = f"{by:04d}-{bm:02d}"

        if (
            SchoolGeneratedInvoice.objects.filter(
                school=school,
                invoice_month_key=new_key,
                status=SchoolGeneratedInvoice.Status.ISSUED,
            )
            .exclude(pk=inv.pk)
            .exists()
        ):
            return JsonResponse(
                {"ok": False, "error": f"Another issued invoice already uses period {new_key}."},
                status=400,
            )

        due_dt = _billing_due_date_for_period(by, bm)
        period = _billing_invoice_period_payload(school, year=by, month=bm if not is_yearly else None)
        inv.billing_period_year = by
        inv.billing_period_month = bm
        inv.invoice_month_key = new_key
        inv.due_date = due_dt
        snap["invoice_month_key"] = new_key
        snap["due_date"] = due_dt.isoformat()
        snap["invoice_period_label"] = period["label"]
        snap["invoice_period_year"] = period["year"]
        snap["invoice_period_month"] = period["month"]
        snap["invoice_period_kind"] = period["kind"]
        snap["invoice_period_intro"] = period["intro_sentence"]
        inv.snapshot = snap
        touched = True

    grand_in = _parse_money(payload.get("grand_total"))
    if grand_in is not None:
        if grand_in < 0:
            return JsonResponse({"ok": False, "error": "grand_total must be >= 0"}, status=400)
        grand = grand_in.quantize(Decimal("0.01"))
        rate = inv.gst_rate_percent or Decimal("0")
        if inv.include_gst and rate > 0:
            subtotal = (grand / (Decimal("1") + rate / Decimal("100"))).quantize(Decimal("0.01"))
            gst_amount = (grand - subtotal).quantize(Decimal("0.01"))
        else:
            subtotal = grand
            gst_amount = Decimal("0")
        inv.grand_total = grand
        inv.subtotal_before_gst = subtotal
        inv.gst_amount = gst_amount
        snap["subtotal_before_gst"] = str(subtotal)
        snap["gst_amount"] = str(gst_amount)
        snap["grand_total"] = str(grand)
        snap["amount_manually_adjusted"] = True
        inv.snapshot = snap
        touched = True

    if not touched:
        return JsonResponse(
            {"ok": False, "error": "Send billing_year and/or billing_month and/or grand_total to update."},
            status=400,
        )

    inv.save()
    _billing_log(
        school,
        SchoolBillingAuditLog.Kind.INVOICE,
        f"Updated generated invoice {inv.invoice_number}",
        {
            "invoice_id": inv.pk,
            "invoice_number": inv.invoice_number,
            "invoice_month_key": inv.invoice_month_key,
            "grand_total": str(inv.grand_total),
        },
        user,
    )
    return JsonResponse(
        {
            "ok": True,
            "invoice": {
                "id": inv.pk,
                "invoice_number": inv.invoice_number,
                "grand_total": str(inv.grand_total),
                "invoice_month_key": inv.invoice_month_key,
            },
        }
    )


def _billing_regenerate_invoice_response(school: School, user, payload: dict) -> JsonResponse:
    try:
        inv_id = int(payload.get("invoice_id"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "invoice_id is required"}, status=400)
    inv = school.generated_invoices.filter(pk=inv_id).first()
    if inv is None:
        return JsonResponse({"ok": False, "error": "Invoice not found"}, status=404)
    if inv.status != SchoolGeneratedInvoice.Status.ISSUED:
        return JsonResponse({"ok": False, "error": "Only issued invoices can be regenerated"}, status=400)
    num = inv.invoice_number
    inv.status = SchoolGeneratedInvoice.Status.VOID
    inv.save(update_fields=["status"])
    _billing_log(
        school,
        SchoolBillingAuditLog.Kind.INVOICE,
        f"Voided invoice {num} for regeneration",
        {"invoice_id": inv_id, "invoice_number": num, "reason": "regenerate_invoice"},
        user,
    )
    by = inv.billing_period_year
    bm = inv.billing_period_month
    if by is None or (school.saas_billing_cycle != School.SaaSBillingCycle.YEARLY and bm is None):
        by, bm = _parse_invoice_month_key(inv.invoice_month_key or "")
    gen_payload: dict = {
        "include_gst": inv.include_gst,
        "gst_rate_percent": str(inv.gst_rate_percent),
        "billing_year": by,
        "ignore_free_service_window": True,
        "ignore_service_start_window": True,
    }
    if school.saas_billing_cycle != School.SaaSBillingCycle.YEARLY:
        gen_payload["billing_month"] = bm if bm is not None else timezone.localdate().month
    return _billing_generate_invoice_response(school, user, gen_payload)


def _billing_student_override_response(school: School, user, raw) -> JsonResponse:
    if raw is None or raw == "" or str(raw).lower() == "null":
        school.billing_student_count_override = None
        school.save(update_fields=["billing_student_count_override"])
        _billing_log(
            school,
            SchoolBillingAuditLog.Kind.STUDENT_OVERRIDE,
            "Cleared student count override (using live tenant count)",
            {"student_count_override": None},
            user,
        )
    else:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "student_count must be an integer"}, status=400)
        if n < 0 or n > 500000:
            return JsonResponse({"ok": False, "error": "student_count out of range"}, status=400)
        if n == 0:
            school.billing_student_count_override = None
            school.save(update_fields=["billing_student_count_override"])
            _billing_log(
                school,
                SchoolBillingAuditLog.Kind.STUDENT_OVERRIDE,
                "Student count override cleared (0 → live count)",
                {"student_count_override": None},
                user,
            )
        else:
            school.billing_student_count_override = n
            school.save(update_fields=["billing_student_count_override"])
            _billing_log(
                school,
                SchoolBillingAuditLog.Kind.STUDENT_OVERRIDE,
                f"Student count override set to {n}",
                {"student_count_override": n},
                user,
            )
    stu, _ = _safe_tenant_counts(school)
    bd = school.saas_billing_monthly_breakdown(stu)
    last_pay = _billing_last_payment_dates_bulk([school.pk]).get(school.pk)
    oldest_map = _billing_oldest_issued_invoice_bulk([school.pk])
    overdue_cut = timezone.now() - timedelta(days=_billing_invoice_overdue_days())
    oldest = oldest_map.get(school.pk)
    pend_over = bool(oldest and oldest < overdue_cut)
    billing = _billing_nested_billing_dict(
        school,
        bd,
        stu,
        last_payment=last_pay,
        pending_overdue=pend_over,
    )
    return JsonResponse({"ok": True, "breakdown": bd, "billing": billing})


def _billing_set_plan_json(school: School, user, plan: Plan) -> JsonResponse:
    old_plan_id = school.plan_id
    old_label = ""
    if school.plan_id:
        try:
            old_label = school.plan.get_name_display()
        except Exception:
            old_label = str(school.plan_id)
    school.plan = plan
    school.save(update_fields=["plan"])
    from apps.core.subscription_access import invalidate_school_feature_cache

    invalidate_school_feature_cache(school.pk)
    if old_plan_id != plan.pk:
        SchoolBillingAuditLog.objects.create(
            school=school,
            kind=SchoolBillingAuditLog.Kind.PLAN_CHANGE,
            summary=f"Plan changed to {plan.get_name_display()}",
            payload={
                "before_plan_id": old_plan_id,
                "after_plan_id": plan.pk,
                "before_plan_label": old_label,
                "after_plan_label": plan.get_name_display(),
            },
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
    stu, _ = _safe_tenant_counts(school)
    bd = school.saas_billing_monthly_breakdown(stu)
    last_pay = _billing_last_payment_dates_bulk([school.pk]).get(school.pk)
    oldest_map = _billing_oldest_issued_invoice_bulk([school.pk])
    overdue_cut = timezone.now() - timedelta(days=_billing_invoice_overdue_days())
    oldest = oldest_map.get(school.pk)
    pend_over = bool(oldest and oldest < overdue_cut)
    billing = _billing_nested_billing_dict(
        school,
        bd,
        stu,
        last_payment=last_pay,
        pending_overdue=pend_over,
    )
    return JsonResponse(
        {
            "ok": True,
            "school_id": school.pk,
            "plan_id": school.plan_id,
            "plan_name": plan.name,
            "breakdown": bd,
            "billing": billing,
        }
    )


def _json_safe(obj):
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def billing_school_details_api(request, school_id: int):
    connection.set_schema_to_public()
    school = get_object_or_404(School.objects.exclude(schema_name="public").select_related("plan"), pk=school_id)
    stu_live, _ = _safe_tenant_counts(school)
    bd = school.saas_billing_monthly_breakdown(stu_live)

    plan_label = ""
    if school.plan_id:
        plan_label = (
            "Premium · Enterprise"
            if school.plan.name == PlanName.PREMIUM
            else school.plan.get_name_display()
        )

    audits = list(
        school.billing_audit_logs.select_related("created_by").order_by("-created_at")[:80]
    )
    audit_json = []
    for a in audits:
        audit_json.append(
            {
                "id": a.pk,
                "kind": a.kind,
                "kind_label": a.get_kind_display(),
                "summary": a.summary,
                "payload": _json_safe(a.payload),
                "created_at": a.created_at.isoformat(),
                "created_by": (a.created_by.get_username() if a.created_by_id else None),
            }
        )

    inv_new = []
    for inv in school.generated_invoices.order_by("-created_at")[:40]:
        snap = inv.snapshot or {}
        inv_key = (inv.invoice_month_key or "").strip() or (snap.get("invoice_month_key") or "")
        hx = _invoice_history_row_extras(inv)
        inv_new.append(
            {
                "id": inv.pk,
                "invoice_number": inv.invoice_number,
                "status": inv.status,
                "payment_state": _generated_invoice_payment_state(inv),
                "grand_total": str(inv.grand_total),
                "base_amount": str(inv.base_amount),
                "extra_amount": str(inv.extra_amount),
                "concession_amount": str(inv.concession_amount),
                "subtotal_before_gst": str(inv.subtotal_before_gst),
                "gst_amount": str(inv.gst_amount),
                "include_gst": inv.include_gst,
                "created_at": inv.created_at.isoformat(),
                "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "invoice_month_key": inv_key,
                "period_label": snap.get("invoice_period_label") or "",
                "complimentary_waiver": bool(snap.get("complimentary_waiver_applied")),
                "month_display": hx["month_display"],
                "status_display": hx["status_display"],
                "paid_date_display": hx["paid_date_display"],
                "badge_status": _invoice_badge_status(inv),
                "payment_received": (inv.snapshot or {}).get("payment_received") or None,
            }
        )

    inv_legacy = []
    for inv in school.platform_invoices.order_by("-year", "-month")[:24]:
        inv_legacy.append(
            {
                "invoice_number": inv.invoice_number,
                "period": f"{inv.year}-{inv.month:02d}",
                "final_amount": str(inv.final_amount),
                "status": inv.status,
                "created_on": inv.created_on.isoformat(),
            }
        )

    payments = []
    for p in school.saas_platform_payments.order_by("-payment_date", "-id")[:30]:
        payments.append(
            {
                "amount": str(p.amount),
                "payment_date": str(p.payment_date),
                "payment_method": p.payment_method,
                "reference": p.reference,
                "notes": p.notes[:200] if p.notes else "",
            }
        )

    inv_payments = []
    for ip in school.platform_invoice_payments.select_related("invoice").order_by("-paid_on")[:30]:
        inv_payments.append(
            {
                "amount_paid": str(ip.amount_paid),
                "paid_on": ip.paid_on.isoformat(),
                "payment_mode": ip.payment_mode,
                "invoice_number": ip.invoice.invoice_number,
            }
        )

    terms_hist = [x for x in audit_json if x["kind"] == SchoolBillingAuditLog.Kind.BILLING_TERMS]
    plan_hist = [x for x in audit_json if x["kind"] == SchoolBillingAuditLog.Kind.PLAN_CHANGE]
    extra_hist = []
    conc_hist = []
    for row in terms_hist:
        after = (row.get("payload") or {}).get("after") or {}
        extra_hist.append(
            {
                "created_at": row["created_at"],
                "billing_extra_per_student_month": after.get("billing_extra_per_student_month"),
                "summary": row.get("summary"),
            }
        )
        conc_hist.append(
            {
                "created_at": row["created_at"],
                "billing_concession_per_student_month": after.get("billing_concession_per_student_month"),
                "summary": row.get("summary"),
            }
        )

    last_pay = _billing_last_payment_dates_bulk([school.pk]).get(school.pk)
    oldest_map = _billing_oldest_issued_invoice_bulk([school.pk])
    overdue_cut = timezone.now() - timedelta(days=_billing_invoice_overdue_days())
    oldest = oldest_map.get(school.pk)
    pend_over = bool(oldest and oldest < overdue_cut)
    billing = _billing_nested_billing_dict(
        school,
        bd,
        stu_live,
        last_payment=last_pay,
        pending_overdue=pend_over,
    )
    today = timezone.localdate()
    preview_y = request.GET.get("preview_year")
    preview_m = request.GET.get("preview_month")
    py = today.year
    pm: int | None = today.month
    if preview_y and str(preview_y).strip().isdigit():
        py = int(str(preview_y).strip())
    if school.saas_billing_cycle != School.SaaSBillingCycle.YEARLY and preview_m and str(preview_m).strip().isdigit():
        pm = max(1, min(12, int(str(preview_m).strip())))
    elif school.saas_billing_cycle == School.SaaSBillingCycle.YEARLY:
        pm = None
    inv_period = _billing_invoice_period_payload(school, year=py, month=pm)
    is_yearly = school.saas_billing_cycle == School.SaaSBillingCycle.YEARLY
    schedule_free = _billing_period_starts_before_commencement(
        school,
        py,
        pm if not is_yearly else None,
        is_yearly,
    )
    eff_bs = _effective_billing_start_date(school)
    comp_active, comp_until = _billing_complimentary_waiver_active(school)

    return JsonResponse(
        {
            "ok": True,
            "invoice_period": inv_period,
            "schedule_free_for_invoice_period": schedule_free,
            "effective_billing_start_date": eff_bs.isoformat() if eff_bs else None,
            "complimentary_waiver_active": comp_active,
            "complimentary_through": comp_until.isoformat() if comp_until else None,
            "school": {
                "id": school.pk,
                "name": school.name,
                "code": school.code,
                "school_status": school.school_status,
                "plan_label": plan_label,
                "plan_id": school.plan_id,
                "billing_student_count_override": school.billing_student_count_override,
                "tenant_student_count": stu_live,
                "saas_billing_auto_renew": school.saas_billing_auto_renew,
                "saas_service_start_date": (
                    school.saas_service_start_date.isoformat() if school.saas_service_start_date else None
                ),
                "saas_free_until_date": (
                    school.saas_free_until_date.isoformat() if school.saas_free_until_date else None
                ),
                "saas_billing_complimentary_until": (
                    school.saas_billing_complimentary_until.isoformat()
                    if school.saas_billing_complimentary_until
                    else None
                ),
                "registration_date": (
                    school.registration_date.isoformat() if getattr(school, "registration_date", None) else None
                ),
                "billing_start_date": (
                    school.billing_start_date.isoformat() if getattr(school, "billing_start_date", None) else None
                ),
                "free_until_date": (
                    school.saas_free_until_date.isoformat() if school.saas_free_until_date else None
                ),
            },
            "breakdown": bd,
            "billing": billing,
            "audit_logs": audit_json,
            "billing_terms_history": terms_hist,
            "plan_change_history": plan_hist,
            "extra_per_student_history": extra_hist,
            "concession_per_student_history": conc_hist,
            "invoices_control_center": inv_new,
            "invoices_legacy": inv_legacy,
            "payment_logs_saas": payments,
            "payment_logs_invoice_allocations": inv_payments,
        }
    )


def _billing_generate_missing_invoices_response(school: School, user) -> JsonResponse:
    """Create issued invoices for billable timeline periods that do not yet have one."""
    stu_live, _ = _safe_tenant_counts(school)
    rows = _billing_build_saas_timeline_rows(school, stu_live)
    created = 0
    errors: list[dict] = []
    for row in rows:
        if not row["is_billable"] or row["invoice_id"]:
            continue
        payload: dict = {"billing_year": row["year"], "include_gst": False}
        if row["month"] is not None:
            payload["billing_month"] = row["month"]
        resp = _billing_generate_invoice_response(school, user, payload)
        try:
            body = json.loads(resp.content.decode())
        except Exception:
            errors.append({"period": row["invoice_month_key"], "error": "Invalid response"})
            continue
        if body.get("ok"):
            created += 1
        else:
            errors.append({"period": row["invoice_month_key"], "error": body.get("error", "Failed")})
    return JsonResponse({"ok": True, "created": created, "errors": errors})


@transaction.non_atomic_requests
@superadmin_required
@require_http_methods(["GET"])
def billing_school_detail(request, school_id: int):
    connection.set_schema_to_public()
    school = get_object_or_404(
        School.objects.exclude(schema_name="public").select_related("plan"),
        pk=school_id,
    )
    stu_live, _ = _safe_tenant_counts(school)
    timeline_rows = _billing_build_saas_timeline_rows(school, stu_live)
    totals = _billing_school_money_totals(school)
    last_pay = _billing_last_payment_dates_bulk([school.pk]).get(school.pk)
    plan_label = _school_plan_label(school)
    eff_bs = _effective_billing_start_date(school)
    free_until = _effective_free_until_date(school)
    reg = getattr(school, "registration_date", None)

    return render(
        request,
        "super_admin/pages/billing_school_detail.html",
        {
            "school": school,
            "plan_label": plan_label,
            "stu_live": stu_live,
            "timeline_rows": timeline_rows,
            "totals": totals,
            "last_payment_date": last_pay,
            "effective_billing_start": eff_bs,
            "free_until_date": free_until,
            "registration_date": reg,
            "billing_list_url": reverse(
                "core:super_admin:control_center_section", kwargs={"section": "billing"}
            ),
            "billing_school_details_url": reverse(
                "core:super_admin:billing_school_details", kwargs={"school_id": school.pk}
            ),
            "billing_school_action_url": reverse(
                "core:super_admin:billing_school_action", kwargs={"school_id": school.pk}
            ),
            "school_master_detail_url": reverse(
                "core:super_admin:school_master_detail", kwargs={"school_id": school.pk}
            ),
        },
    )


@transaction.non_atomic_requests
@superadmin_required
@require_http_methods(["GET", "POST"])
def billing_school_receive_payment(request, school_id: int, invoice_id: int):
    """Full-page form to record payment for a generated SaaS invoice (creates subscription payment row)."""
    connection.set_schema_to_public()
    school = get_object_or_404(
        School.objects.exclude(schema_name="public").select_related("plan"),
        pk=school_id,
    )
    inv = (
        school.generated_invoices.filter(pk=invoice_id)
        .exclude(status=SchoolGeneratedInvoice.Status.VOID)
        .first()
    )
    detail_back = reverse("core:super_admin:billing_school_detail", kwargs={"school_id": school.pk})
    if inv is None:
        messages.error(request, "Invoice not found.")
        return redirect(detail_back)

    if inv.status != SchoolGeneratedInvoice.Status.ISSUED:
        messages.warning(request, "This invoice is not open for payment (already paid or void).")
        return redirect(detail_back)

    if request.method == "POST":
        paid_raw = (request.POST.get("paid_at") or "").strip()
        note = (request.POST.get("notes") or "").strip()[:2000]
        pm = request.POST.get("payment_method") or SaaSPlatformPayment.PaymentMethod.UPI
        ref = (request.POST.get("reference") or "").strip()
        internal = (request.POST.get("internal_receipt_no") or "").strip()
        paid_dt: datetime | None = None
        if paid_raw:
            d_only = parse_date(paid_raw[:10])
            if d_only is None:
                messages.error(request, "Invalid received-on date.")
                return redirect(request.path)
            paid_dt = timezone.make_aware(datetime.combine(d_only, datetime.min.time()))
        try:
            with transaction.atomic():
                inv_locked = (
                    school.generated_invoices.select_for_update()
                    .filter(pk=inv.pk)
                    .exclude(status=SchoolGeneratedInvoice.Status.VOID)
                    .first()
                )
                if inv_locked is None:
                    raise ValueError("Invoice not found.")
                if inv_locked.status != SchoolGeneratedInvoice.Status.ISSUED:
                    raise ValueError("This invoice is no longer open for payment.")
                adj_err = _billing_apply_receive_payment_invoice_adjustments(
                    school, inv_locked, request.POST, request.user
                )
                if adj_err:
                    raise ValueError(adj_err)
                _apply_school_generated_invoice_paid(
                    school,
                    inv_locked,
                    request.user,
                    paid_at_dt=paid_dt,
                    notes=note,
                    payment_method=pm,
                    reference=ref,
                    internal_receipt_no=internal,
                    create_ledger=True,
                    use_atomic=False,
                )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(request.path)
        messages.success(request, f"Recorded payment for {inv.invoice_number}.")
        return redirect(detail_back)

    return render(
        request,
        "super_admin/pages/billing_receive_payment.html",
        {
            "school": school,
            "invoice": inv,
            "plan_label": _school_plan_label(school),
            "billing_detail_url": detail_back,
            "payment_method_choices": SaaSPlatformPayment.PaymentMethod.choices,
            "today_iso": timezone.localdate().isoformat(),
        },
    )


def _billing_round_grand_total_rupees(grand: Decimal, nearest: int) -> Decimal:
    """Round tax-inclusive grand total to nearest ₹1 or ₹10 (0 = only paise)."""
    from decimal import ROUND_HALF_UP

    g = grand.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if nearest == 10:
        return (g / Decimal("10")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal("10")
    if nearest == 1:
        return g.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return g


def _billing_set_invoice_tax_totals_from_grand(inv: SchoolGeneratedInvoice, grand: Decimal, snap: dict) -> None:
    """Set subtotal_before_gst, gst_amount, grand_total from inclusive grand (matches invoice update logic)."""
    from decimal import ROUND_HALF_UP

    rate = inv.gst_rate_percent or Decimal("0")
    grand = grand.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if inv.include_gst and rate > 0:
        subtotal = (grand / (Decimal("1") + rate / Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        gst_amount = (grand - subtotal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        subtotal = grand
        gst_amount = Decimal("0").quantize(Decimal("0.01"))
    inv.grand_total = grand
    inv.subtotal_before_gst = subtotal
    inv.gst_amount = gst_amount
    snap["subtotal_before_gst"] = str(subtotal)
    snap["gst_amount"] = str(gst_amount)
    snap["grand_total"] = str(grand)
    snap["amount_manually_adjusted"] = True


def _billing_apply_receive_payment_invoice_adjustments(
    school: School,
    inv: SchoolGeneratedInvoice,
    post,
    user,
) -> str | None:
    """
    Apply optional line / discount / rounding / grand override from receive-payment POST.
    Persists invoice row and audit log. Returns error message or None.
    """
    from decimal import ROUND_HALF_UP

    q2 = Decimal("0.01")
    snap = dict(inv.snapshot or {})
    old_grand = inv.grand_total
    old_base = inv.base_amount
    old_extra = inv.extra_amount
    old_conc = inv.concession_amount

    use_override = (post.get("use_grand_override") or "").strip().lower() in ("1", "true", "on", "yes")
    grand_ov = _parse_money(post.get("grand_override"))
    if use_override:
        if grand_ov is None:
            return "Enter a valid final grand total, or turn off the override."
        if grand_ov < 0:
            return "Final grand total cannot be negative."
        _billing_set_invoice_tax_totals_from_grand(inv, grand_ov, snap)
        snap["receive_adjustment"] = {
            "mode": "grand_override",
            "notes": "Totals recomputed from inclusive grand total on receive-payment screen.",
        }
        inv.snapshot = snap
        inv.save(
            update_fields=[
                "subtotal_before_gst",
                "gst_amount",
                "grand_total",
                "snapshot",
            ]
        )
        if inv.grand_total != old_grand:
            _billing_log(
                school,
                SchoolBillingAuditLog.Kind.INVOICE,
                f"Adjusted invoice {inv.invoice_number} before payment (grand override)",
                {"invoice_id": inv.pk, "grand_total": str(inv.grand_total)},
                user,
            )
        return None

    raw_b = (post.get("invoice_line_base") or "").strip()
    raw_e = (post.get("invoice_line_extra") or "").strip()
    raw_c = (post.get("invoice_line_concession") or "").strip()
    b = _parse_money(raw_b) if raw_b != "" else inv.base_amount
    e = _parse_money(raw_e) if raw_e != "" else inv.extra_amount
    c = _parse_money(raw_c) if raw_c != "" else inv.concession_amount
    if b is None or e is None or c is None:
        return "Invalid amount in base, extra, or concession."
    b = b.quantize(q2, rounding=ROUND_HALF_UP)
    e = e.quantize(q2, rounding=ROUND_HALF_UP)
    c = c.quantize(q2, rounding=ROUND_HALF_UP)
    if b < 0 or e < 0 or c < 0:
        return "Base, extra, and concession must be zero or positive."
    max_conc = (b + e).quantize(q2, rounding=ROUND_HALF_UP)
    if c > max_conc:
        return "Concession cannot exceed base plus extra."

    sub = (b + e - c).quantize(q2, rounding=ROUND_HALF_UP)
    disc = _parse_money(post.get("settlement_discount"))
    if disc is None:
        disc = Decimal("0")
    if disc < 0:
        return "Settlement discount cannot be negative."
    disc = disc.quantize(q2, rounding=ROUND_HALF_UP)
    sub = (sub - disc).quantize(q2, rounding=ROUND_HALF_UP)
    if sub < 0:
        return "Settlement discount is larger than the net amount before tax."

    rate = inv.gst_rate_percent or Decimal("0")
    if inv.include_gst and rate > 0:
        gst_amount = (sub * (rate / Decimal("100"))).quantize(q2, rounding=ROUND_HALF_UP)
        grand_pre = (sub + gst_amount).quantize(q2, rounding=ROUND_HALF_UP)
    else:
        gst_amount = Decimal("0").quantize(q2, rounding=ROUND_HALF_UP)
        grand_pre = sub

    rn = (post.get("round_nearest") or "").strip()
    nearest = int(rn) if rn in ("1", "10") else 0
    grand_final = _billing_round_grand_total_rupees(grand_pre, nearest)
    _billing_set_invoice_tax_totals_from_grand(inv, grand_final, snap)

    inv.base_amount = b
    inv.extra_amount = e
    inv.concession_amount = c

    snap["base_cost"] = str(b)
    snap["extra_charges"] = str(e)
    snap["concession_amount"] = str(c)
    snap["receive_adjustment"] = {
        "mode": "line_and_settlement",
        "settlement_discount": str(disc),
        "round_nearest_rupees": nearest,
        "computed_pre_round_grand": str(grand_pre),
    }
    inv.snapshot = snap

    inv.save(
        update_fields=[
            "base_amount",
            "extra_amount",
            "concession_amount",
            "subtotal_before_gst",
            "gst_amount",
            "grand_total",
            "snapshot",
        ]
    )
    if inv.grand_total != old_grand or b != old_base or e != old_extra or c != old_conc or disc > 0 or nearest:
        _billing_log(
            school,
            SchoolBillingAuditLog.Kind.INVOICE,
            f"Adjusted invoice {inv.invoice_number} before payment (lines / settlement / rounding)",
            {
                "invoice_id": inv.pk,
                "base_amount": str(b),
                "extra_amount": str(e),
                "concession_amount": str(c),
                "grand_total": str(inv.grand_total),
                "settlement_discount": str(disc),
                "round_nearest_rupees": nearest,
            },
            user,
        )
    return None


def _normalize_saas_payment_method(raw: str | None) -> str:
    allowed = {c[0] for c in SaaSPlatformPayment.PaymentMethod.choices}
    k = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "neft": SaaSPlatformPayment.PaymentMethod.BANK_TRANSFER,
        "rtgs": SaaSPlatformPayment.PaymentMethod.BANK_TRANSFER,
        "imps": SaaSPlatformPayment.PaymentMethod.BANK_TRANSFER,
        "bank": SaaSPlatformPayment.PaymentMethod.BANK_TRANSFER,
    }
    if k in aliases:
        k = aliases[k]
    if k in allowed:
        return k
    return SaaSPlatformPayment.PaymentMethod.UPI


def _apply_school_generated_invoice_paid(
    school: School,
    inv: SchoolGeneratedInvoice,
    user,
    *,
    paid_at_dt: datetime | None,
    notes: str,
    payment_method: str | None,
    reference: str,
    internal_receipt_no: str,
    create_ledger: bool = True,
    use_atomic: bool = True,
) -> None:
    if inv.status != SchoolGeneratedInvoice.Status.ISSUED:
        raise ValueError("Only issued invoices can be marked paid")
    pm = _normalize_saas_payment_method(payment_method)
    pm_display = dict(SaaSPlatformPayment.PaymentMethod.choices).get(pm, pm)
    note_clean = (notes or "").strip()[:2000]
    ref_clean = (reference or "").strip()[:200]
    int_receipt = (internal_receipt_no or "").strip()[:64]
    if paid_at_dt is None:
        paid_at_dt = timezone.now()
    elif timezone.is_naive(paid_at_dt):
        paid_at_dt = timezone.make_aware(paid_at_dt, timezone.get_current_timezone())
    pay_date = paid_at_dt.date()

    ledger_bits = [f"Generated invoice {inv.invoice_number}"]
    if (inv.invoice_month_key or "").strip():
        ledger_bits.append(f"Period {inv.invoice_month_key.strip()}")
    if note_clean:
        ledger_bits.append(note_clean)
    ledger_notes = " · ".join(ledger_bits)[:4000]

    with (transaction.atomic() if use_atomic else nullcontext()):
        inv_locked = (
            SchoolGeneratedInvoice.objects.select_for_update()
            .filter(pk=inv.pk, school_id=school.pk)
            .first()
        )
        if inv_locked is None:
            raise ValueError("Invoice not found for this school")
        if inv_locked.status != SchoolGeneratedInvoice.Status.ISSUED:
            raise ValueError("Invoice is no longer issued (it may already be paid).")
        inv_locked.status = SchoolGeneratedInvoice.Status.PAID
        inv_locked.paid_at = paid_at_dt
        inv_locked.paid_notes = note_clean
        snap = dict(inv_locked.snapshot or {})
        snap["payment_received"] = {
            "payment_method": pm,
            "payment_method_display": pm_display,
            "reference": ref_clean,
            "internal_receipt_no": int_receipt,
            "notes": note_clean,
            "recorded_at": timezone.now().isoformat(),
        }
        inv_locked.snapshot = snap
        inv_locked.save(update_fields=["status", "paid_at", "paid_notes", "snapshot"])

        if create_ledger and not SaaSPlatformPayment.objects.filter(school_generated_invoice_id=inv_locked.pk).exists():
            SaaSPlatformPayment.objects.create(
                school=school,
                amount=inv_locked.grand_total,
                payment_date=pay_date,
                payment_method=pm,
                reference=ref_clean,
                notes=ledger_notes,
                internal_receipt_no=int_receipt,
                school_generated_invoice=inv_locked,
                recorded_by=user if getattr(user, "is_authenticated", False) else None,
            )

    _billing_log(
        school,
        SchoolBillingAuditLog.Kind.PAYMENT,
        f"Marked invoice {inv_locked.invoice_number} as paid ({pm_display})",
        {
            "invoice_id": inv_locked.pk,
            "invoice_number": inv_locked.invoice_number,
            "notes": note_clean,
            "paid_at": inv_locked.paid_at.isoformat() if inv_locked.paid_at else None,
            "payment_method": pm,
            "reference": ref_clean,
            "internal_receipt_no": int_receipt,
        },
        user,
    )


def _billing_mark_paid_response(school: School, user, payload: dict) -> JsonResponse:
    inv_id = payload.get("invoice_id")
    inv = None
    if inv_id not in (None, ""):
        try:
            inv = school.generated_invoices.filter(pk=int(inv_id)).first()
        except (TypeError, ValueError):
            inv = None
    if inv is None:
        inv = (
            school.generated_invoices.filter(status=SchoolGeneratedInvoice.Status.ISSUED)
            .order_by("-created_at")
            .first()
        )
    if inv is None:
        return JsonResponse({"ok": False, "error": "No issued invoice to mark as paid"}, status=400)
    note = (payload.get("notes") or "").strip()[:2000]
    paid_at_raw = payload.get("paid_at")
    paid_dt: datetime | None = None
    if paid_at_raw:
        d_only = parse_date(str(paid_at_raw).strip()[:10])
        if d_only is None:
            return JsonResponse({"ok": False, "error": "Invalid paid_at (use YYYY-MM-DD)"}, status=400)
        paid_dt = timezone.make_aware(datetime.combine(d_only, datetime.min.time()))
    pm_raw = payload.get("payment_method")
    ref = (payload.get("reference") or "").strip()
    internal = (payload.get("internal_receipt_no") or "").strip()
    try:
        _apply_school_generated_invoice_paid(
            school,
            inv,
            user,
            paid_at_dt=paid_dt,
            notes=note,
            payment_method=str(pm_raw) if pm_raw is not None else None,
            reference=ref,
            internal_receipt_no=internal,
            create_ledger=True,
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "invoice_id": inv.pk, "invoice_number": inv.invoice_number})


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def billing_school_action_api(request, school_id: int):
    connection.set_schema_to_public()
    school = get_object_or_404(School.objects.exclude(schema_name="public").select_related("plan"), pk=school_id)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "error": "Body must be a JSON object"}, status=400)

    action = (payload.get("action") or "").strip().lower()
    user = request.user

    if action == "suspend":
        school.school_status = School.SchoolStatus.SUSPENDED
        school.save(update_fields=["school_status"])
        _billing_log(
            school,
            SchoolBillingAuditLog.Kind.STATUS,
            "School suspended from Control Center",
            {"school_status": school.school_status},
            user,
        )
        return JsonResponse({"ok": True, "school_status": school.school_status})

    if action == "activate":
        school.school_status = School.SchoolStatus.ACTIVE
        school.save(update_fields=["school_status"])
        _billing_log(
            school,
            SchoolBillingAuditLog.Kind.STATUS,
            "School activated from Control Center",
            {"school_status": school.school_status},
            user,
        )
        return JsonResponse({"ok": True, "school_status": school.school_status})

    if action == "set_student_override":
        return _billing_student_override_response(school, user, payload.get("student_count"))

    if action == "generate_invoice":
        return _billing_generate_invoice_response(school, user, payload)

    if action == "update_generated_invoice":
        return _billing_update_generated_invoice_response(school, user, payload)

    if action == "regenerate_invoice":
        return _billing_regenerate_invoice_response(school, user, payload)

    if action == "mark_paid":
        return _billing_mark_paid_response(school, user, payload)

    if action == "generate_missing_invoices":
        return _billing_generate_missing_invoices_response(school, user)

    return JsonResponse({"ok": False, "error": "Unknown action"}, status=400)


def _billing_json_body(request) -> tuple[dict | None, JsonResponse | None]:
    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return None, JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    if not isinstance(payload, dict):
        return None, JsonResponse({"ok": False, "error": "Body must be a JSON object"}, status=400)
    return payload, None


def _billing_school_from_body_id(payload: dict):
    raw = payload.get("school_id")
    try:
        sid = int(raw)
    except (TypeError, ValueError):
        return None, JsonResponse({"ok": False, "error": "school_id is required"}, status=400)
    school = School.objects.exclude(schema_name="public").select_related("plan").filter(pk=sid).first()
    if school is None:
        return None, JsonResponse({"ok": False, "error": "School not found"}, status=404)
    return school, None


def _billing_serialize_school_row(
    school: School,
    stu: int,
    bd: dict,
    *,
    last_billing_at,
    has_pending_invoice: bool,
    pending_invoice_count: int,
    pending_invoice_total: Decimal,
    last_payment_date: date | None,
    pending_payment_overdue: bool,
) -> dict:
    plan_slug = str(school.plan.name) if school.plan_id else "none"
    plan_label = ""
    if school.plan_id:
        plan_label = (
            "Premium · Enterprise"
            if school.plan.name == PlanName.PREMIUM
            else school.plan.get_name_display()
        )
    billing = _billing_nested_billing_dict(
        school,
        bd,
        stu,
        last_payment=last_payment_date,
        pending_overdue=pending_payment_overdue,
    )
    comp_waived, _ = _billing_complimentary_waiver_active(school)
    eff_row = _effective_billing_start_date(school)
    return {
        "id": school.pk,
        "name": school.name,
        "code": school.code,
        "school_status": school.school_status,
        "plan_id": school.plan_id,
        "plan_slug": plan_slug,
        "plan_label": plan_label,
        "saas_billing_cycle": school.saas_billing_cycle,
        "saas_billing_auto_renew": school.saas_billing_auto_renew,
        "saas_billing_complimentary_until": (
            school.saas_billing_complimentary_until.isoformat()
            if school.saas_billing_complimentary_until
            else None
        ),
        "saas_service_start_date": (
            school.saas_service_start_date.isoformat() if school.saas_service_start_date else None
        ),
        "saas_free_until_date": school.saas_free_until_date.isoformat() if school.saas_free_until_date else None,
        "registration_date": (
            school.registration_date.isoformat() if getattr(school, "registration_date", None) else None
        ),
        "billing_start_date": (
            school.billing_start_date.isoformat() if getattr(school, "billing_start_date", None) else None
        ),
        "effective_billing_start_date": eff_row.isoformat() if eff_row else None,
        "free_until_date": school.saas_free_until_date.isoformat() if school.saas_free_until_date else None,
        "complimentary_waiver_active": comp_waived,
        "billing_extra_per_student_month": str(school.billing_extra_per_student_month),
        "billing_concession_per_student_month": str(school.billing_concession_per_student_month),
        "billing_student_count_override": school.billing_student_count_override,
        "breakdown": bd,
        "billing": billing,
        "has_pending_invoice": has_pending_invoice,
        "pending_invoice_count": pending_invoice_count,
        "pending_invoice_total": format(Decimal(pending_invoice_total or 0).quantize(Decimal("0.01")), "f"),
        "pending_payment_overdue": pending_payment_overdue,
        "last_billing_at": last_billing_at.isoformat() if last_billing_at else None,
    }


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def schools_billing_config_list_api(request):
    """GET …/api/schools/ — billing date fields per school (public schema)."""
    connection.set_schema_to_public()
    rows = School.objects.exclude(schema_name="public").select_related("plan").order_by("name")[:500]
    return JsonResponse({"ok": True, "schools": [_billing_school_dates_public(s) for s in rows]})


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def schools_update_billing_config_api(request):
    """
    POST …/api/schools/update-billing-config/
    JSON: { "school_id", "registration_date"?, "free_until_date"? | "saas_free_until_date"?, "billing_start_date"? }
    """
    connection.set_schema_to_public()
    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "error": "Body must be a JSON object"}, status=400)
    school, err_resp = _billing_school_from_body_id(payload)
    if err_resp:
        return err_resp
    cfg_keys = (
        "registration_date",
        "billing_start_date",
        "saas_free_until_date",
        "free_until_date",
    )
    patch = {k: payload[k] for k in cfg_keys if k in payload}
    if not patch:
        return JsonResponse({"ok": False, "error": "No billing configuration fields in body"}, status=400)
    return _billing_apply_terms_patch(school, request.user, patch)


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def billing_schools_list_api(request):
    """
    GET …/api/billing/schools/?q=&plan=basic|pro|premium|none&status=&cycle=&sort=
    Equivalent REST path segment: billing/schools/
    """
    connection.set_schema_to_public()
    q = (request.GET.get("q") or "").strip()
    plan = (request.GET.get("plan") or "").strip().lower()
    status = (request.GET.get("status") or "").strip().lower()
    cycle = (request.GET.get("cycle") or "").strip().lower()
    sort = (request.GET.get("sort") or "").strip().lower()

    base_qs = School.objects.exclude(schema_name="public").select_related("plan")
    if q:
        base_qs = base_qs.filter(name__icontains=q)
    if plan == "none":
        base_qs = base_qs.filter(plan__isnull=True)
    elif plan in (PlanName.BASIC, PlanName.PRO, PlanName.PREMIUM):
        base_qs = base_qs.filter(plan__name=plan)
    if status in {c[0] for c in School.SchoolStatus.choices}:
        base_qs = base_qs.filter(school_status=status)
    if cycle in {c[0] for c in School.SaaSBillingCycle.choices}:
        base_qs = base_qs.filter(saas_billing_cycle=cycle)

    latest_audit_sq = Subquery(
        SchoolBillingAuditLog.objects.filter(school_id=OuterRef("pk"))
        .order_by("-created_at")
        .values("created_at")[:1]
    )
    pending_inv_exists = Exists(
        SchoolGeneratedInvoice.objects.filter(
            school_id=OuterRef("pk"),
            status=SchoolGeneratedInvoice.Status.ISSUED,
        )
    )
    rows_qs = base_qs.annotate(
        last_billing_at=latest_audit_sq,
        has_pending_invoice=pending_inv_exists,
    ).order_by("name")[:500]
    schools_list = list(rows_qs)
    school_ids = [s.pk for s in schools_list]
    pending_agg = {
        r["school_id"]: r
        for r in SchoolGeneratedInvoice.objects.filter(
            status=SchoolGeneratedInvoice.Status.ISSUED,
            school_id__in=school_ids,
        ).values("school_id").annotate(c=Count("id"), amt=Sum("grand_total"))
    }
    last_pay_map = _billing_last_payment_dates_bulk(school_ids)
    oldest_issued_map = _billing_oldest_issued_invoice_bulk(school_ids)
    overdue_cut = timezone.now() - timedelta(days=_billing_invoice_overdue_days())

    out_rows = []
    mrr_sum = Decimal("0")
    active_ct = 0
    total_students_live = 0
    pending_amount_all = Decimal("0")
    pending_inv_all = 0
    for sch in schools_list:
        stu, _ = _safe_tenant_counts(sch)
        bd = sch.saas_billing_monthly_breakdown(stu)
        mrr_sum += Decimal(bd["final_monthly"])
        if sch.school_status == School.SchoolStatus.ACTIVE:
            active_ct += 1
        total_students_live += int(bd["tenant_student_count"])
        pa = pending_agg.get(sch.pk) or {}
        pc = int(pa.get("c") or 0)
        pamt = pa.get("amt") or Decimal("0")
        pending_inv_all += pc
        pending_amount_all += Decimal(pamt or 0)
        last_ts = getattr(sch, "last_billing_at", None)
        oldest = oldest_issued_map.get(sch.pk)
        pend_over = bool(oldest and oldest < overdue_cut)
        out_rows.append(
            _billing_serialize_school_row(
                sch,
                stu,
                bd,
                last_billing_at=last_ts,
                has_pending_invoice=bool(getattr(sch, "has_pending_invoice", False)),
                pending_invoice_count=pc,
                pending_invoice_total=Decimal(pamt or 0),
                last_payment_date=last_pay_map.get(sch.pk),
                pending_payment_overdue=pend_over,
            )
        )

    n = len(out_rows)
    if n:
        sorted_mrr = sorted(
            (Decimal(r["breakdown"]["final_monthly"]) for r in out_rows),
            reverse=True,
        )
        k = max(1, math.ceil(n * 0.2))
        thr = sorted_mrr[k - 1]
        for r in out_rows:
            r["high_revenue"] = Decimal(r["breakdown"]["final_monthly"]) >= thr
    else:
        for r in out_rows:
            r["high_revenue"] = False

    if sort == "revenue_high":
        out_rows.sort(key=lambda r: Decimal(r["breakdown"]["final_monthly"]), reverse=True)
    elif sort == "revenue_low":
        out_rows.sort(key=lambda r: Decimal(r["breakdown"]["final_monthly"]))
    elif sort == "updated_recent":
        out_rows.sort(
            key=lambda r: (r["last_billing_at"] is None, r["last_billing_at"] or ""),
            reverse=True,
        )

    summary = {
        "total_revenue_monthly": format(mrr_sum.quantize(Decimal("0.01")), "f"),
        "active_schools": active_ct,
        "pending_payments_amount": format(pending_amount_all.quantize(Decimal("0.01")), "f"),
        "pending_invoices_count": pending_inv_all,
        "total_students": total_students_live,
    }
    return JsonResponse({"ok": True, "summary": summary, "schools": out_rows})


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def billing_api_update_extra(request):
    connection.set_schema_to_public()
    payload, err = _billing_json_body(request)
    if err:
        return err
    school, err = _billing_school_from_body_id(payload)
    if err:
        return err
    amt = payload.get("billing_extra_per_student_month")
    if amt is None and "amount" in payload:
        amt = payload.get("amount")
    if amt is None:
        return JsonResponse(
            {"ok": False, "error": "billing_extra_per_student_month or amount is required"},
            status=400,
        )
    return _billing_apply_terms_patch(school, request.user, {"billing_extra_per_student_month": amt})


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def billing_api_update_concession(request):
    connection.set_schema_to_public()
    payload, err = _billing_json_body(request)
    if err:
        return err
    school, err = _billing_school_from_body_id(payload)
    if err:
        return err
    amt = payload.get("billing_concession_per_student_month")
    if amt is None and "amount" in payload:
        amt = payload.get("amount")
    if amt is None:
        return JsonResponse(
            {"ok": False, "error": "billing_concession_per_student_month or amount is required"},
            status=400,
        )
    return _billing_apply_terms_patch(school, request.user, {"billing_concession_per_student_month": amt})


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def billing_api_update_plan(request):
    connection.set_schema_to_public()
    payload, err = _billing_json_body(request)
    if err:
        return err
    school, err = _billing_school_from_body_id(payload)
    if err:
        return err
    raw_pid = payload.get("plan_id")
    try:
        pid = int(raw_pid)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "plan_id is required"}, status=400)
    plan = Plan.objects.filter(pk=pid, is_active=True).first()
    if plan is None:
        return JsonResponse({"ok": False, "error": "Plan not found"}, status=404)
    return _billing_set_plan_json(school, request.user, plan)


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def billing_api_update_students(request):
    connection.set_schema_to_public()
    payload, err = _billing_json_body(request)
    if err:
        return err
    school, err = _billing_school_from_body_id(payload)
    if err:
        return err
    raw = payload.get("student_count")
    if raw is None and "billing_student_count_override" in payload:
        raw = payload.get("billing_student_count_override")
    return _billing_student_override_response(school, request.user, raw)


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def billing_api_generate_invoice(request):
    connection.set_schema_to_public()
    payload, err = _billing_json_body(request)
    if err:
        return err
    school, err = _billing_school_from_body_id(payload)
    if err:
        return err
    return _billing_generate_invoice_response(school, request.user, payload)


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def billing_api_invoices_list(request):
    """
    GET …/api/billing/invoices/?school_id=&month=
    ``month``: ``YYYY-MM`` (monthly) or ``YYYY-00`` (yearly key), optional — omit for recent history.
    """
    connection.set_schema_to_public()
    raw_sid = (request.GET.get("school_id") or "").strip()
    if not raw_sid:
        return JsonResponse({"ok": False, "error": "school_id is required"}, status=400)
    try:
        sid = int(raw_sid)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid school_id"}, status=400)
    school = School.objects.exclude(schema_name="public").select_related("plan").filter(pk=sid).first()
    if school is None:
        return JsonResponse({"ok": False, "error": "School not found"}, status=404)
    month = (request.GET.get("month") or "").strip()
    qs = school.generated_invoices.all().order_by("-billing_period_year", "-billing_period_month", "-created_at")
    if month:
        qs = qs.filter(invoice_month_key=month)
    rows = [_invoice_public_api_dict(inv) for inv in qs[:200]]
    return JsonResponse(
        {
            "ok": True,
            "school": _billing_school_dates_public(school),
            "invoices": rows,
        }
    )


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def billing_api_mark_paid(request):
    """POST …/api/billing/mark-paid/ — JSON body ``school_id``, optional ``invoice_id``, ``notes``, ``paid_at``."""
    connection.set_schema_to_public()
    payload, err = _billing_json_body(request)
    if err:
        return err
    school, err = _billing_school_from_body_id(payload)
    if err:
        return err
    return _billing_mark_paid_response(school, request.user, payload)


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def billing_api_update_invoice_period(request):
    """
    POST …/api/billing/update-invoice-period/
    Body: ``school_id``, ``invoice_id``, ``billing_year``, optional ``billing_month`` (monthly schools).
    """
    connection.set_schema_to_public()
    payload, err = _billing_json_body(request)
    if err:
        return err
    school, err = _billing_school_from_body_id(payload)
    if err:
        return err
    try:
        inv_id = int(payload.get("invoice_id"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "invoice_id is required"}, status=400)
    inv = school.generated_invoices.filter(pk=inv_id).first()
    if inv is None:
        return JsonResponse({"ok": False, "error": "Invoice not found"}, status=404)
    inner: dict = {"invoice_id": inv_id}
    for k in ("billing_year", "billing_month", "grand_total"):
        if k in payload:
            inner[k] = payload[k]
    if not any(k in inner for k in ("billing_year", "billing_month", "grand_total")):
        return JsonResponse(
            {
                "ok": False,
                "error": "Provide at least one of: billing_year, billing_month, grand_total",
            },
            status=400,
        )
    return _billing_update_generated_invoice_response(school, request.user, inner)


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def billing_export_csv(request):
    connection.set_schema_to_public()
    q = (request.GET.get("q") or "").strip()
    plan = (request.GET.get("plan") or "").strip().lower()
    status = (request.GET.get("status") or "").strip().lower()
    cycle = (request.GET.get("cycle") or "").strip().lower()

    base_qs = School.objects.exclude(schema_name="public").select_related("plan")
    if q:
        base_qs = base_qs.filter(name__icontains=q)
    if plan == "none":
        base_qs = base_qs.filter(plan__isnull=True)
    elif plan in (PlanName.BASIC, PlanName.PRO, PlanName.PREMIUM):
        base_qs = base_qs.filter(plan__name=plan)
    if status in {c[0] for c in School.SchoolStatus.choices}:
        base_qs = base_qs.filter(school_status=status)
    if cycle in {c[0] for c in School.SaaSBillingCycle.choices}:
        base_qs = base_qs.filter(saas_billing_cycle=cycle)

    latest_audit_sq = Subquery(
        SchoolBillingAuditLog.objects.filter(school_id=OuterRef("pk"))
        .order_by("-created_at")
        .values("created_at")[:1]
    )
    pending_inv_exists = Exists(
        SchoolGeneratedInvoice.objects.filter(
            school_id=OuterRef("pk"),
            status=SchoolGeneratedInvoice.Status.ISSUED,
        )
    )
    schools_list = list(
        base_qs.annotate(
            last_billing_at=latest_audit_sq,
            has_pending_invoice=pending_inv_exists,
        ).order_by("name")[:2000]
    )
    school_ids = [s.pk for s in schools_list]
    last_pay_map = _billing_last_payment_dates_bulk(school_ids)
    oldest_map = _billing_oldest_issued_invoice_bulk(school_ids)
    overdue_cut = timezone.now() - timedelta(days=_billing_invoice_overdue_days())
    pending_agg = {
        r["school_id"]: r
        for r in SchoolGeneratedInvoice.objects.filter(
            status=SchoolGeneratedInvoice.Status.ISSUED,
            school_id__in=school_ids,
        ).values("school_id").annotate(c=Count("id"), amt=Sum("grand_total"))
    }

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="saas_billing_schools.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(
        [
            "school_id",
            "school_name",
            "plan_name",
            "price_per_student",
            "total_students_live",
            "billed_students",
            "extra_per_student",
            "concession_per_student",
            "billing_cycle",
            "status",
            "last_payment_date",
            "final_monthly_mrr",
            "final_yearly_equiv",
            "pending_invoice_count",
            "pending_invoice_total",
            "pending_overdue",
            "auto_renew",
        ]
    )
    for sch in schools_list:
        stu, _ = _safe_tenant_counts(sch)
        bd = sch.saas_billing_monthly_breakdown(stu)
        plan_name = ""
        if sch.plan_id:
            plan_name = (
                "Premium · Enterprise"
                if sch.plan.name == PlanName.PREMIUM
                else sch.plan.get_name_display()
            )
        else:
            plan_name = "No plan"
        lp = last_pay_map.get(sch.pk)
        oldest = oldest_map.get(sch.pk)
        overdue = bool(oldest and oldest < overdue_cut)
        pa = pending_agg.get(sch.pk) or {}
        pc = int(pa.get("c") or 0)
        pamt = pa.get("amt") or Decimal("0")
        writer.writerow(
            [
                sch.pk,
                sch.name,
                plan_name,
                bd["plan_price_per_student"],
                bd["tenant_student_count"],
                bd["student_count"],
                bd["billing_extra_per_student_month"],
                bd["billing_concession_per_student_month"],
                sch.saas_billing_cycle,
                sch.school_status,
                lp.isoformat() if lp else "",
                bd["final_monthly"],
                bd["final_period"],
                pc,
                format(Decimal(pamt or 0).quantize(Decimal("0.01")), "f"),
                "yes" if overdue else "no",
                "yes" if sch.saas_billing_auto_renew else "no",
            ]
        )
    return response


@transaction.non_atomic_requests
@superadmin_required
@require_POST
def billing_send_payment_reminder_api(request):
    connection.set_schema_to_public()
    payload, err = _billing_json_body(request)
    if err:
        return err
    school, err = _billing_school_from_body_id(payload)
    if err:
        return err
    inv = (
        school.generated_invoices.filter(status=SchoolGeneratedInvoice.Status.ISSUED)
        .order_by("-created_at")
        .first()
    )
    if inv is None:
        return JsonResponse({"ok": False, "error": "No issued invoice pending payment"}, status=400)
    recipient = (school.contact_email or "").strip()
    if not recipient:
        return JsonResponse({"ok": False, "error": "School has no contact_email"}, status=400)
    body = (
        f"Dear {(school.contact_person or school.name).strip()},\n\n"
        f"This is a payment reminder for {school.name} ({school.code}).\n"
        f"Invoice: {inv.invoice_number}\n"
        f"Amount due: INR {inv.grand_total}\n\n"
        f"Please remit payment and reply with payment reference when done.\n\n"
        f"— {get_platform_product_name()} billing"
    )
    try:
        send_mail(
            subject=f"Payment reminder — {school.name}",
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None) or "noreply@example.com",
            recipient_list=[recipient],
            fail_silently=False,
        )
    except Exception as exc:
        return JsonResponse({"ok": False, "error": f"Email send failed: {exc}"}, status=502)
    _billing_log(
        school,
        SchoolBillingAuditLog.Kind.PAYMENT,
        f"Payment reminder email sent for {inv.invoice_number}",
        {"invoice_id": inv.pk, "invoice_number": inv.invoice_number, "to": recipient},
        request.user,
    )
    return JsonResponse(
        {
            "ok": True,
            "email_sent": True,
            "recipient": recipient,
            "invoice_number": inv.invoice_number,
            "sms_sent": False,
            "sms_note": "SMS is not wired for platform billing reminders in this deployment.",
        }
    )


@transaction.non_atomic_requests
@superadmin_required
@require_GET
def billing_coupons_list_api(request):
    connection.set_schema_to_public()
    today = timezone.now().date()
    qs = (
        Coupon.objects.filter(is_active=True)
        .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today))
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
        .order_by("code")[:200]
    )
    data = [
        {
            "code": c.code,
            "discount_type": c.discount_type,
            "discount_value": str(c.discount_value),
            "max_usage": c.max_usage,
            "used_count": c.used_count,
            "valid_from": c.valid_from.isoformat() if c.valid_from else None,
            "valid_to": c.valid_to.isoformat() if c.valid_to else None,
        }
        for c in qs
    ]
    return JsonResponse({"ok": True, "coupons": data})

