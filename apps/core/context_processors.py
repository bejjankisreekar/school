"""Template context processors."""

from django.conf import settings
from django.urls import NoReverseMatch, reverse


def app_branding(request):
    """
    Product / company name for global chrome (navbar center, left title when no school).

    Uses ``settings.APP_PRODUCT_NAME`` only (env ``APP_PRODUCT_NAME``, default "Campus ERP").
    School ``header_text`` is not shown in the top bar — it is for branding forms / profile
    and other surfaces, not the main nav product line.
    """
    name = getattr(settings, "APP_PRODUCT_NAME", None) or "Campus ERP"
    return {"app_product_name": name}


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
        from apps.core.utils import has_feature_access

        role = (getattr(user, "role", "") or "").strip() or "STUDENT"
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

        def allowed(it: SidebarMenuItem) -> bool:
            fc = (it.feature_code or "").strip()
            if not fc:
                return True
            return bool(has_feature_access(school, fc, user=user))

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

        # Build nodes for allowed items only (and drop broken routes).
        nodes: dict[int, dict] = {}
        for it in items:
            if not allowed(it):
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
