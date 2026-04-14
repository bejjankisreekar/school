"""
Super Admin Control Center — /super-admin/control-center/
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db import connection, transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.urls import NoReverseMatch, reverse

from apps.accounts.decorators import superadmin_required
from apps.accounts.models import User
from apps.customers.models import Plan, SaaSPlatformPayment, School
from apps.core.models import SidebarMenuItem

from .control_center import (
    CONTROL_MODULE_DEFS,
    CONTROL_PLAN_TIERS,
    CONTROL_SCHOOL_ROLES,
    DURATION_CHOICES,
    ROLE_PAGE_OPTIONS,
    TIER_TO_PLAN_NAME,
    get_control_meta,
    merge_control_meta,
)
from .platform_financials import build_super_admin_platform_snapshot, summarize_billing_rows


def _parse_int_optional(val: str, default: int | None = None) -> int | None:
    if val is None or str(val).strip() == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _parse_date_optional(val: str) -> str | None:
    v = (val or "").strip()
    if not v:
        return None
    try:
        date.fromisoformat(v)
        return v
    except ValueError:
        return None


@transaction.non_atomic_requests
@superadmin_required
@require_http_methods(["GET", "POST"])
def superadmin_control_center(request):
    connection.set_schema_to_public()
    if request.method == "POST":
        return _handle_post(request)
    return _handle_get(request)


def _handle_post(request):
    action = (request.POST.get("action") or "").strip()
    school_id = _parse_int_optional(request.POST.get("school_id"))

    if action == "assign_tier" and school_id:
        school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
        tier = (request.POST.get("tier") or "").strip().lower()
        plan_name = TIER_TO_PLAN_NAME.get(tier)
        if not plan_name:
            messages.error(request, "Invalid plan tier.")
            return redirect("core:superadmin_control_center")
        plan = Plan.objects.filter(name=plan_name, is_active=True).first()
        if not plan:
            messages.error(
                request,
                f"No active Plan named “{plan_name}”. Run: python manage.py seed_saas_plans",
            )
            return redirect("core:superadmin_control_center")
        school.saas_plan = plan
        school.enabled_features_override = None
        meta = dict(get_control_meta(school))
        meta["assigned_tier"] = tier
        meta["duration"] = (request.POST.get("duration") or "yearly").strip()
        ps = _parse_date_optional(request.POST.get("plan_start") or "")
        pe = _parse_date_optional(request.POST.get("plan_expires") or "")
        if ps:
            meta["plan_start"] = ps
        if pe:
            meta["plan_expires"] = pe
        school.platform_control_meta = meta
        school.save()
        messages.success(request, f"{school.name}: plan set to {plan.name} ({tier.title()}).")
        return redirect("core:superadmin_control_center")

    if action == "save_features" and school_id:
        school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
        selected = request.POST.getlist("features")
        valid = {c for c, _ in CONTROL_MODULE_DEFS}
        cleaned = [c for c in selected if c in valid]
        school.enabled_features_override = cleaned if cleaned else None
        school.save()
        messages.success(request, f"Module access updated for {school.name}.")
        return redirect("core:superadmin_control_center")

    if action == "save_status" and school_id:
        school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
        st = (request.POST.get("school_status") or "").strip()
        choices = {c[0] for c in School.SchoolStatus.choices}
        if st in choices:
            school.school_status = st
            school.save()
            messages.success(request, f"Status set to {school.get_school_status_display()} for {school.name}.")
        else:
            messages.error(request, "Invalid status.")
        return redirect("core:superadmin_control_center")

    if action == "save_platform_flags" and school_id:
        school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
        meta = dict(get_control_meta(school))
        meta["disable_login"] = request.POST.get("disable_login") == "on"
        meta["modules_locked"] = request.POST.get("modules_locked") == "on"
        meta["duration"] = (request.POST.get("duration") or meta.get("duration") or "yearly").strip()
        ps = _parse_date_optional(request.POST.get("plan_start") or "")
        pe = _parse_date_optional(request.POST.get("plan_expires") or "")
        if ps:
            meta["plan_start"] = ps
        elif "plan_start" in request.POST and not ps:
            meta.pop("plan_start", None)
        if pe:
            meta["plan_expires"] = pe
        elif "plan_expires" in request.POST and not pe:
            meta.pop("plan_expires", None)
        meta["limits"] = {
            "max_students": _parse_int_optional(request.POST.get("max_students")),
            "max_teachers": _parse_int_optional(request.POST.get("max_teachers")),
            "storage_gb": _parse_int_optional(request.POST.get("storage_gb")),
        }
        meta["limit_flags"] = {
            "payroll": request.POST.get("lf_payroll") == "on",
            "exams": request.POST.get("lf_exams") == "on",
            "analytics": request.POST.get("lf_analytics") == "on",
            "mobile_api": request.POST.get("lf_mobile_api") == "on",
        }
        school.platform_control_meta = meta
        school.save()
        messages.success(request, f"Platform controls saved for {school.name}.")
        return redirect("core:superadmin_control_center")

    if action == "save_role_permissions" and school_id:
        school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
        meta = dict(get_control_meta(school))
        perms: dict[str, list[str]] = {}
        valid_pages = {c for c, _ in ROLE_PAGE_OPTIONS}
        for role_key, _label in CONTROL_SCHOOL_ROLES:
            key = f"rp_{role_key}"
            chosen = [p for p in request.POST.getlist(key) if p in valid_pages]
            if chosen:
                perms[role_key] = chosen
        meta["role_permissions"] = perms
        school.platform_control_meta = meta
        school.save()
        messages.success(request, f"Role access matrix saved for {school.name} (stored for enforcement pipeline).")
        return redirect("core:superadmin_control_center")

    if action == "suspend" and school_id:
        school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
        school.school_status = School.SchoolStatus.SUSPENDED
        school.save()
        messages.warning(request, f"Suspended: {school.name}")
        return redirect("core:superadmin_control_center")

    if action == "activate" and school_id:
        school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
        school.school_status = School.SchoolStatus.ACTIVE
        school.save()
        messages.success(request, f"Activated: {school.name}")
        return redirect("core:superadmin_control_center")

    if action == "delete_school" and school_id:
        school = get_object_or_404(School.objects.exclude(schema_name="public"), pk=school_id)
        confirm = (request.POST.get("confirm_code") or "").strip()
        if confirm != school.code:
            messages.error(request, "Type the school code exactly to confirm deletion.")
            return redirect("core:superadmin_control_center")
        name = school.name
        try:
            school.delete(force_drop=True)
            messages.success(request, f"Deleted tenant and school: {name}.")
        except Exception as exc:
            messages.error(request, f"Could not delete: {exc}")
        return redirect("core:superadmin_control_center")

    messages.error(request, "Unknown action.")
    return redirect("core:superadmin_control_center")


def _handle_get(request):
    q = (request.GET.get("q") or "").strip()
    snap = build_super_admin_platform_snapshot()
    billing_summary = summarize_billing_rows(snap["billing_rows"])

    schools = (
        School.objects.exclude(schema_name="public")
        .select_related("saas_plan", "plan")
        .order_by("name")
    )
    if q:
        schools = schools.filter(Q(name__icontains=q) | Q(code__icontains=q))

    schools_list = list(schools)
    rows = []
    for school in schools_list:
        admin = (
            User.objects.filter(school=school, role=User.Roles.ADMIN)
            .order_by("id")
            .first()
        )
        active_users = User.objects.filter(school=school, is_active=True).count()
        meta = get_control_meta(school)
        exp = school.trial_end_date
        if meta.get("plan_expires"):
            try:
                d = date.fromisoformat(meta["plan_expires"])
                exp = d
            except (TypeError, ValueError):
                pass
        rows.append(
            {
                "school": school,
                "admin": admin,
                "admin_name": admin.get_full_name() or admin.username if admin else "—",
                "active_users": active_users,
                "plan_expiry": exp,
                "enabled_codes": list(school.get_enabled_feature_codes()),
                "meta": meta,
                "role_permissions_json": json.dumps(meta.get("role_permissions") or {}),
            }
        )

    revenue = SaaSPlatformPayment.objects.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    active_plans = sum(1 for r in rows if r["school"].saas_plan_id)
    def _meta_expired(m: dict) -> bool:
        pe = m.get("plan_expires")
        if not pe or not isinstance(pe, str):
            return False
        try:
            return date.fromisoformat(pe) < date.today()
        except ValueError:
            return False

    expired_count = sum(
        1 for r in rows if r["school"].is_trial_expired() or _meta_expired(r["meta"])
    )

    plans = list(Plan.objects.filter(is_active=True).order_by("price_per_student", "name"))

    from django.conf import settings

    return render(
        request,
        "superadmin/control_center.html",
        {
            "page_title": "Super Admin Control Center",
            "page_subtitle": "Manage schools, plans, access permissions, and platform controls",
            "search_q": q,
            "metric_total_schools": snap["total_schools"],
            "metric_active_plans": active_plans,
            "metric_expired_schools": expired_count,
            "metric_total_students": snap["total_students"],
            "metric_revenue": revenue,
            "billing_summary": billing_summary,
            "school_rows": rows,
            "control_plan_tiers": CONTROL_PLAN_TIERS,
            "duration_choices": DURATION_CHOICES,
            "module_defs": CONTROL_MODULE_DEFS,
            "school_roles": CONTROL_SCHOOL_ROLES,
            "role_page_options": ROLE_PAGE_OPTIONS,
            "school_status_choices": School.SchoolStatus.choices,
            "plans_catalog": plans,
            "debug": settings.DEBUG,
        },
    )


@transaction.non_atomic_requests
@superadmin_required
@require_http_methods(["GET", "POST"])
def superadmin_sidebar_management(request):
    """
    Super Admin → Sidebar Management.
    Allows updating order/visibility/icon/label/route/parent for role menus.
    """
    connection.set_schema_to_public()

    role = (request.GET.get("role") or request.POST.get("role") or SidebarMenuItem.Role.ADMIN).strip().upper()
    valid_roles = {c[0] for c in SidebarMenuItem.Role.choices}
    if role not in valid_roles:
        role = SidebarMenuItem.Role.ADMIN

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "reorder":
            # Expect ids[] in the new order (top-level items only or full list view order).
            ids = request.POST.getlist("ids[]") or request.POST.getlist("ids")
            cleaned = []
            for raw in ids:
                try:
                    cleaned.append(int(raw))
                except (TypeError, ValueError):
                    continue
            # Only reorder items for this role (ignore foreign ids).
            existing = set(
                SidebarMenuItem.objects.filter(role=role, id__in=cleaned).values_list("id", flat=True)
            )
            order = 1
            with transaction.atomic():
                for _id in cleaned:
                    if _id not in existing:
                        continue
                    SidebarMenuItem.objects.filter(id=_id).update(display_order=order)
                    order += 1
            messages.success(request, "Sidebar order updated.")
            return redirect(f"{reverse('core:superadmin_sidebar_management')}?role={role}")

        if action == "update":
            item_id = request.POST.get("item_id")
            try:
                item_id_int = int(item_id)
            except (TypeError, ValueError):
                messages.error(request, "Invalid item.")
                return redirect(f"{reverse('core:superadmin_sidebar_management')}?role={role}")

            item = get_object_or_404(SidebarMenuItem, id=item_id_int, role=role)

            label = (request.POST.get("label") or "").strip()
            icon = (request.POST.get("icon") or "").strip()
            route_name = (request.POST.get("route_name") or "").strip()
            href = (request.POST.get("href") or "").strip()
            feature_code = (request.POST.get("feature_code") or "").strip()
            is_visible = request.POST.get("is_visible") == "on"
            parent_id = (request.POST.get("parent_id") or "").strip()

            parent = None
            if parent_id:
                try:
                    pid = int(parent_id)
                    if pid != item.id:
                        parent = SidebarMenuItem.objects.filter(role=role, id=pid).first()
                except (TypeError, ValueError):
                    parent = None

            # Validate reverse if route_name present
            route_ok = True
            if route_name:
                try:
                    reverse(route_name)
                except NoReverseMatch:
                    route_ok = False

            if route_name and not route_ok:
                messages.error(request, f"Route name cannot be resolved: {route_name}")
            else:
                if label:
                    item.label = label
                item.icon = icon
                item.route_name = route_name
                item.href = href
                item.feature_code = feature_code
                item.is_visible = is_visible
                item.parent = parent
                item.save(update_fields=["label", "icon", "route_name", "href", "feature_code", "is_visible", "parent"])
                messages.success(request, "Menu item updated.")

            return redirect(f"{reverse('core:superadmin_sidebar_management')}?role={role}")

        if action == "create":
            label = (request.POST.get("label") or "").strip() or "New item"
            route_name = (request.POST.get("route_name") or "").strip()
            href = (request.POST.get("href") or "").strip()
            icon = (request.POST.get("icon") or "").strip()
            feature_code = (request.POST.get("feature_code") or "").strip()
            parent_id = (request.POST.get("parent_id") or "").strip()

            parent = None
            if parent_id:
                try:
                    pid = int(parent_id)
                    parent = SidebarMenuItem.objects.filter(role=role, id=pid).first()
                except (TypeError, ValueError):
                    parent = None

            if route_name:
                try:
                    reverse(route_name)
                except NoReverseMatch:
                    messages.error(request, f"Route name cannot be resolved: {route_name}")
                    return redirect(f"{reverse('core:superadmin_sidebar_management')}?role={role}")

            max_order = (
                SidebarMenuItem.objects.filter(role=role, parent=parent).order_by("-display_order").values_list("display_order", flat=True).first()
                or 0
            )
            SidebarMenuItem.objects.create(
                role=role,
                label=label,
                route_name=route_name,
                href=href,
                icon=icon,
                feature_code=feature_code,
                parent=parent,
                display_order=max_order + 1,
                is_visible=True,
                is_active=True,
                created_by=request.user,
                modified_by=request.user,
            )
            messages.success(request, "Menu item created.")
            return redirect(f"{reverse('core:superadmin_sidebar_management')}?role={role}")

        if action == "seed_defaults":
            if SidebarMenuItem.objects.filter(role=role).exists():
                messages.info(request, "Menu already exists for this role. Use reset via management command if needed.")
                return redirect(f"{reverse('core:superadmin_sidebar_management')}?role={role}")
            messages.info(request, "Run: python manage.py seed_sidebar_menu (or --reset) to seed defaults.")
            return redirect(f"{reverse('core:superadmin_sidebar_management')}?role={role}")

        messages.error(request, "Unknown action.")
        return redirect(f"{reverse('core:superadmin_sidebar_management')}?role={role}")

    items = (
        SidebarMenuItem.objects.filter(role=role)
        .select_related("parent")
        .order_by("parent_id", "display_order", "id")
    )
    parents = SidebarMenuItem.objects.filter(role=role, parent__isnull=True).order_by("display_order", "id")

    return render(
        request,
        "superadmin/sidebar_management.html",
        {
            "role": role,
            "role_choices": SidebarMenuItem.Role.choices,
            "items": items,
            "parent_choices": parents,
        },
    )
