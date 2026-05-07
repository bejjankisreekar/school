"""Template context processors."""

from django.db import connection
from django.db.utils import OperationalError, ProgrammingError
from django.urls import NoReverseMatch, reverse

from apps.core.branding import get_platform_product_name


def app_branding(request):
    """
    Product / company name for global chrome (sidebar, marketing, login, page titles).

    Resolved via ``get_platform_product_name()`` (Control Center platform name or env default).
    """
    data = {"app_product_name": get_platform_product_name(), "inbox_unread_count": 0, "inbox_url": ""}
    try:
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return data

        role = getattr(user, "role", "")
        if role == "TEACHER":
            from apps.school_data.models import StudentMessage
            from apps.notifications.models import Message as InternalMessage

            student_unread = StudentMessage.objects.filter(receiver=user, is_read=False).count()
            try:
                admin_unread = InternalMessage.objects.filter(receiver=user, is_read=False).count()
            except (ProgrammingError, OperationalError):
                admin_unread = 0
            data["inbox_unread_count"] = int(student_unread) + int(admin_unread)
            data["inbox_url"] = reverse("core:teacher_messages")
        elif role == "STUDENT":
            from apps.school_data.models import StudentMessage

            data["inbox_unread_count"] = StudentMessage.objects.filter(receiver=user, is_read=False).count()
            data["inbox_url"] = reverse("core:student_messages")
        elif role == "SUPERADMIN":
            # Top-bar “Messages” opens platform inbox (school admins), not internal admin_messages.
            try:
                # IMPORTANT: never leave the connection on public schema during template render.
                # Using schema_context auto-restores the previous tenant schema.
                from django_tenants.utils import schema_context

                with schema_context("public"):
                    from apps.platform_messaging import services as _platform_msg

                    data["inbox_unread_count"] = int(_platform_msg.unread_count_for_superadmin())
            except Exception:
                data["inbox_unread_count"] = 0
            try:
                data["inbox_url"] = reverse("core:super_admin:platform_messages")
            except NoReverseMatch:
                data["inbox_url"] = ""
        elif role == "ADMIN":
            from apps.notifications.models import Message as InternalMessage
            from apps.platform_messaging.access import school_admin_can_use_platform_messaging

            internal_unread = 0
            try:
                internal_unread = int(
                    InternalMessage.objects.filter(receiver=user, is_read=False).count()
                )
            except (ProgrammingError, OperationalError):
                internal_unread = 0

            platform_unread = 0
            if school_admin_can_use_platform_messaging(user):
                try:
                    # IMPORTANT: never leave the connection on public schema during template render.
                    from django_tenants.utils import schema_context

                    with schema_context("public"):
                        from apps.platform_messaging import services as _platform_msg

                        _pk = _platform_msg.resolve_school_pk_for_user(user)
                        if _pk is not None:
                            platform_unread = int(_platform_msg.unread_count_for_school_admin(_pk))
                except Exception:
                    platform_unread = 0

            if school_admin_can_use_platform_messaging(user):
                data["inbox_unread_count"] = internal_unread + platform_unread
            else:
                data["inbox_unread_count"] = 0

            if school_admin_can_use_platform_messaging(user):
                try:
                    data["inbox_url"] = reverse("core:school_admin_platform_messages")
                except NoReverseMatch:
                    data["inbox_url"] = reverse("notifications:admin_messages")
            else:
                # Internal `/school/messages/` uses the same plan feature as platform messaging.
                data["inbox_url"] = ""
    except Exception:
        return data
    return data


def sidebar_menu(request):
    """
    Provide role-based sidebar menu tree as `sidebar_menu_tree`.

    Falls back to legacy hardcoded sidebar when no rows exist for the role.
    """
    try:
        user = getattr(request, "user", None)
        if not request or not user or not getattr(user, "is_authenticated", False):
            return {}

        from apps.core.models import SidebarMenuItem
        from apps.core.plan_features import feature_granted
        from apps.core.utils import has_feature_access

        role = (getattr(user, "role", "") or "").strip() or "STUDENT"

        # Ensure Teacher messages entry exists for older seeded sidebars.
        if role == "TEACHER":
            try:
                from apps.core.models import SidebarMenuItem

                item, _ = SidebarMenuItem.objects.get_or_create(
                    role="TEACHER",
                    route_name="core:teacher_messages",
                    defaults={
                        "label": "Messages",
                        "icon": "bi bi-chat-dots",
                        "display_order": 8,
                        "feature_code": "",
                        "href": "",
                        "parent": None,
                        "is_visible": True,
                        "is_active": True,
                    },
                )
                changed = False
                if item.is_visible is False:
                    item.is_visible = True
                    changed = True
                if item.is_active is False:
                    item.is_active = True
                    changed = True
                if (item.feature_code or "").strip():
                    item.feature_code = ""
                    changed = True
                if changed:
                    item.save(update_fields=["is_visible", "is_active", "feature_code"])
            except Exception:
                pass

        qs = (
            SidebarMenuItem.objects.filter(role=role, is_active=True, is_visible=True)
            .select_related("parent")
            .order_by("parent_id", "display_order", "id")
        )
        items = list(qs)
        if not items:
            return {"sidebar_menu_tree": None}

        school = getattr(user, "school", None)
        view_name = None
        try:
            rm = getattr(request, "resolver_match", None)
            view_name = getattr(rm, "view_name", None) if rm else None
        except Exception:
            view_name = None
        req_path = (getattr(request, "path", "") or "").rstrip("/") or "/"

        def student_sidebar_excluded(it: SidebarMenuItem) -> bool:
            # Student UX: Profile + Notifications are available in the top bar,
            # so hide them from the left sidebar to avoid duplication.
            if role != "STUDENT":
                return False
            rn = (it.route_name or "").strip()
            href = (it.href or "").strip()
            label = (it.label or "").strip().lower()
            if rn in {
                "notifications:student_notifications",
                "core:student_profile",
                "core:student_profile_settings",
                "core:edit_profile",
                "core:edit_profile_web",
                "accounts:account_profile",
            }:
                return True
            if label in {"profile", "my profile", "notifications"}:
                return True
            if href.startswith("/student/profile") or href.startswith("/student-dashboard/profile") or href.startswith("/notifications/student"):
                return True
            return False

        def resolve_href(it: SidebarMenuItem) -> str:
            rn = (it.route_name or "").strip()
            if rn:
                try:
                    return reverse(rn)
                except NoReverseMatch:
                    # Some routes require args (e.g. student exam session detail).
                    # For those, SidebarMenuItem should provide an href prefix (e.g. "/student/exam/session/"),
                    # so the menu still renders and highlights via path-prefix match.
                    return (it.href or "").strip()
            return (it.href or "").strip()

        def is_active_item(route_name: str, href: str) -> bool:
            """
            Mark menu item active for the current request.
            Prefer resolver_match.view_name equality, but fallback to path prefix match
            so href-only menu rows still highlight correctly after refresh.
            """
            rn = (route_name or "").strip()
            if view_name and rn and view_name == rn:
                return True
            h = (href or "").rstrip("/")
            if not h:
                return False
            # Normalize root and allow sub-paths (e.g. /school/staff-attendance/mark/ highlights /school/staff-attendance/)
            h_norm = h or "/"
            if req_path == h_norm or req_path.startswith(h_norm + "/"):
                return True
            # Student exams: keep the Exams menu highlighted on session/exam detail pages too.
            # Works even when the menu row href is /student/exams/ and the current page is /student/exam/session/<id>/.
            if h_norm.startswith("/student/exams") and req_path.startswith("/student/exam/"):
                return True
            return False

        feats = getattr(request, "school_features", None)

        def item_allowed(it: SidebarMenuItem) -> bool:
            fc = (it.feature_code or "").strip()
            if not fc:
                return True
            if feats is not None:
                return feature_granted(feats, fc)
            return bool(has_feature_access(school, fc, user=user))

        # Build nodes: hide anything not on the school's plan (no disabled rows in nav).
        nodes: dict[int, dict] = {}
        for it in items:
            if student_sidebar_excluded(it):
                continue
            if not item_allowed(it):
                continue
            href = resolve_href(it)
            if not href:
                continue
            nodes[it.id] = {
                "id": it.id,
                "label": it.label,
                "icon": (it.icon or "").strip(),
                "href": href,
                "route_name": (it.route_name or "").strip(),
                "parent_id": it.parent_id,
                "children": [],
                "active": is_active_item((it.route_name or "").strip(), href),
            }

        tree: list[dict] = []
        for node in nodes.values():
            pid = node["parent_id"]
            if pid and pid in nodes:
                nodes[pid]["children"].append(node)
            else:
                tree.append(node)

        def prune_empty_groups(items_list: list[dict]) -> list[dict]:
            out = []
            for n in items_list:
                raw_ch = n.get("children") or []
                pruned_ch = prune_empty_groups(raw_ch) if raw_ch else []
                n = {**n, "children": pruned_ch}
                if pruned_ch:
                    out.append(n)
                elif (n.get("href") or "").strip():
                    out.append(n)
            return out

        tree = prune_empty_groups(tree)

        # Mark parent active if any descendant active.
        def bubble_active(n: dict) -> bool:
            any_child = False
            for ch in n["children"]:
                if bubble_active(ch):
                    any_child = True
            if any_child:
                n["active"] = True
            n["has_children"] = bool(n["children"])
            return bool(n["active"])

        for top in tree:
            bubble_active(top)

        # Keep ordering as inserted from queryset sort; children appended in that order.
        return {"sidebar_menu_tree": tree}
    except Exception:
        # Never break a page due to sidebar config.
        return {"sidebar_menu_tree": None}



def active_academic_year(request):
    """Expose the active academic year and the available list to templates.

    Adds two variables for every authenticated tenant request:

    * ``current_academic_year`` -- the active ``AcademicYear`` instance (or ``None``)
    * ``available_academic_years`` -- ordered list for the navbar selector

    Anonymous users / requests without a school get empty values. Resolution is
    cached per-request by :func:`apps.core.active_academic_year.get_active_academic_year`.
    """
    try:
        from apps.core.active_academic_year import (
            get_active_academic_year,
            list_available_academic_years,
        )

        ay = get_active_academic_year(request)
        years = list_available_academic_years(request) if ay or getattr(request, "user", None) else []
        return {
            "current_academic_year": ay,
            "available_academic_years": years,
        }
    except Exception:
        return {"current_academic_year": None, "available_academic_years": []}
