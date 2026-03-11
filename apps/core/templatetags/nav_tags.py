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


@register.filter
def school_has_feature(request, feature):
    """Return True if user's school has the given plan feature. Use: request|school_has_feature:'fees'"""
    if not request or not getattr(request, "user", None) or not request.user.is_authenticated:
        return False
    features = getattr(request, "school_features", None)
    if features is not None:
        return feature in features
    school = getattr(request.user, "school", None)
    if not school:
        return False
    return school.has_feature(feature)


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
