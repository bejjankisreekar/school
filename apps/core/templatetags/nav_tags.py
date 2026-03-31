"""Template tags for sidebar navigation active state."""
from django import template

register = template.Library()


@register.filter
def branding_initials(name):
    """Two-letter school mark (e.g. Green Valley School → GV); fallback SE."""
    if not name:
        return "SE"
    s = str(name).strip()
    if not s:
        return "SE"
    parts = s.split()
    if len(parts) >= 2 and parts[0] and parts[1]:
        return (parts[0][0] + parts[1][0]).upper()
    if len(s) >= 2:
        return s[:2].upper()
    return (s[0] * 2).upper()


@register.filter
def first_word(value):
    """First whitespace-delimited word, for display names."""
    if not value:
        return ""
    parts = str(value).strip().split()
    return parts[0] if parts else ""


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


@register.filter
def feature_access(request, feature_code: str):
    """Superadmin: all modules. Others: school plan. Use: request|feature_access:'attendance'"""
    from apps.core.utils import has_feature_access as _has_feature_access

    if not request or not getattr(request, "user", None) or not request.user.is_authenticated:
        return False
    school = getattr(request.user, "school", None)
    return _has_feature_access(school, feature_code, user=request.user)


@register.filter
def has_feature_access(school, feature_code: str):
    """Return True if `school` has `feature_code` enabled (no request user — prefer request|feature_access)."""
    from apps.core.utils import has_feature_access as _has_feature_access
    return _has_feature_access(school, feature_code)


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
