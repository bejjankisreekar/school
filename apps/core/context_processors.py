"""Template context processors."""

from django.conf import settings


def app_branding(request):
    """
    Product / company name for global chrome (navbar center, left title when no school).

    Uses ``settings.APP_PRODUCT_NAME`` only (env ``APP_PRODUCT_NAME``, default "Campus ERP").
    School ``header_text`` is not shown in the top bar — it is for branding forms / profile
    and other surfaces, not the main nav product line.
    """
    name = getattr(settings, "APP_PRODUCT_NAME", None) or "Campus ERP"
    return {"app_product_name": name}
