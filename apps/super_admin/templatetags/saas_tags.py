from django import template

from apps.super_admin.utils import has_feature

register = template.Library()


@register.simple_tag(takes_context=True)
def has_feature_access(context, feature_code: str) -> bool:
    request = context.get("request")
    user = getattr(request, "user", None)
    return has_feature(user, feature_code)

