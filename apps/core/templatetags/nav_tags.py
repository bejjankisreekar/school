"""Template tags for sidebar navigation active state."""
from django import template

register = template.Library()


@register.simple_tag
def nav_active(request, *path_prefixes):
    """Return 'sidebar-nav-active' if current path matches any prefix."""
    if not request or not hasattr(request, "path"):
        return ""
    path = (request.path or "").rstrip("/") or "/"
    for prefix in path_prefixes:
        p = (prefix or "").rstrip("/")
        if not p:
            continue
        if path == p or path.startswith(p + "/"):
            return "sidebar-nav-active"
    return ""


@register.simple_tag
def nav_active_names(request, *url_names):
    """Return 'sidebar-nav-active' if current view's url_name matches any."""
    if not request or not hasattr(request, "resolver_match") or not request.resolver_match:
        return ""
    current = getattr(request.resolver_match, "url_name", None)
    if not current:
        return ""
    for name in url_names:
        if name and current == name:
            return "sidebar-nav-active"
    return ""
