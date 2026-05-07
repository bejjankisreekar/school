"""
Global active academic year selection for the School ERP.

Single source of truth for "which academic year is the current user looking at?"
Resolution order on every request:

1. ``request.session["active_academic_year_id"]`` if it points to an AcademicYear
   that exists in the user's tenant schema.
2. The tenant's ``AcademicYear`` flagged ``is_active=True`` (most recent).
3. The most recent ``AcademicYear`` for the tenant (highest ``start_date``).
4. ``None`` if the tenant has no academic years yet.

Usage from views:

    from apps.core.active_academic_year import get_active_academic_year
    ay = get_active_academic_year(request)
    if ay:
        qs = qs.filter(academic_year=ay)

In templates the context processor exposes ``current_academic_year`` and
``available_academic_years``.

Forms can subclass :class:`AcademicYearFormMixin` to auto-default the
``academic_year`` field from the request.
"""
from __future__ import annotations

import logging

from django.utils.functional import SimpleLazyObject

logger = logging.getLogger(__name__)

SESSION_KEY = "active_academic_year_id"
_REQUEST_CACHE_ATTR = "_active_academic_year_cache"


def _resolve_school(request):
    """Return the user's School (public schema row) or None."""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "school", None)


def _fetch_in_tenant(school, ay_pk: int | None):
    """Return ``(selected_ay, fallback_active_ay, latest_ay)`` from the tenant schema."""
    from django_tenants.utils import tenant_context

    from apps.school_data.models import AcademicYear

    selected = active = latest = None
    try:
        with tenant_context(school):
            if ay_pk:
                selected = AcademicYear.objects.filter(pk=ay_pk).first()
            active = (
                AcademicYear.objects.filter(is_active=True)
                .order_by("-start_date")
                .first()
            )
            latest = AcademicYear.objects.order_by("-start_date").first()
    except Exception:
        logger.exception(
            "active_academic_year fetch failed schema=%s",
            getattr(school, "schema_name", ""),
        )
    return selected, active, latest


def get_active_academic_year(request):
    """Resolve and cache the active ``AcademicYear`` instance for this request."""
    cached = getattr(request, _REQUEST_CACHE_ATTR, None)
    if cached is not None:
        return cached.get("ay")

    school = _resolve_school(request)
    if school is None:
        setattr(request, _REQUEST_CACHE_ATTR, {"ay": None, "school": None})
        return None

    selected_id = None
    session = getattr(request, "session", None)
    if session is not None:
        try:
            raw = session.get(SESSION_KEY)
            if raw is not None:
                selected_id = int(raw)
        except (TypeError, ValueError):
            selected_id = None

    selected, active, latest = _fetch_in_tenant(school, selected_id)
    ay = selected or active or latest

    # Forget a stale session id (year deleted, or now belongs to another tenant).
    if session is not None and selected_id and not selected:
        try:
            session.pop(SESSION_KEY, None)
        except Exception:
            pass

    setattr(request, _REQUEST_CACHE_ATTR, {"ay": ay, "school": school})
    logger.debug(
        "get_active_academic_year user=%s school=%s ay=%s source=%s",
        getattr(getattr(request, "user", None), "id", None),
        getattr(school, "schema_name", ""),
        getattr(ay, "pk", None),
        "session" if selected else ("active_flag" if active else ("latest" if latest else "none")),
    )
    return ay


def list_available_academic_years(request) -> list:
    """Materialized list of academic years for the current tenant (for dropdowns)."""
    school = _resolve_school(request)
    if school is None:
        return []
    from django_tenants.utils import tenant_context

    from apps.school_data.models import AcademicYear

    try:
        with tenant_context(school):
            return list(
                AcademicYear.objects.only("pk", "name", "is_active", "start_date").order_by(
                    "-start_date"
                )
            )
    except Exception:
        logger.exception(
            "list_available_academic_years failed schema=%s",
            getattr(school, "schema_name", ""),
        )
        return []


def set_active_academic_year(request, ay_pk: int | str | None) -> bool:
    """Persist ``ay_pk`` in session if it identifies a real academic year for this tenant."""
    school = _resolve_school(request)
    if school is None or not hasattr(request, "session"):
        return False
    if ay_pk in (None, "", "None"):
        request.session.pop(SESSION_KEY, None)
        request.session.modified = True
        if hasattr(request, _REQUEST_CACHE_ATTR):
            delattr(request, _REQUEST_CACHE_ATTR)
        return True
    try:
        ay_pk = int(ay_pk)
    except (TypeError, ValueError):
        return False

    from django_tenants.utils import tenant_context

    from apps.school_data.models import AcademicYear

    try:
        with tenant_context(school):
            exists = AcademicYear.objects.filter(pk=ay_pk).exists()
    except Exception:
        logger.exception(
            "set_active_academic_year tenant lookup failed schema=%s",
            getattr(school, "schema_name", ""),
        )
        return False
    if not exists:
        return False
    request.session[SESSION_KEY] = ay_pk
    request.session.modified = True
    if hasattr(request, _REQUEST_CACHE_ATTR):
        delattr(request, _REQUEST_CACHE_ATTR)
    logger.info(
        "set_active_academic_year user=%s school=%s ay=%s",
        getattr(getattr(request, "user", None), "id", None),
        getattr(school, "schema_name", ""),
        ay_pk,
    )
    return True


def attach_lazy_to_request(request) -> None:
    """Attach ``request.academic_year`` as a lazy object (resolves on first access)."""
    if hasattr(request, "academic_year"):
        return
    request.academic_year = SimpleLazyObject(lambda: get_active_academic_year(request))


def filter_by_academic_year(qs, request, field_name: str = "academic_year"):
    """
    Convenience: filter ``qs`` by ``request.academic_year`` if available.

    Returns the queryset unchanged when no academic year is selected so callers can
    still render lists for tenants that haven't configured years yet.
    """
    ay = get_active_academic_year(request)
    if not ay:
        return qs
    return qs.filter(**{field_name: ay})


class AcademicYearFormMixin:
    """
    ModelForm/Form helper that auto-fills ``academic_year`` from the request.

    Pop ``request`` (or ``academic_year``) when instantiating, e.g.::

        form = ClassRoomForm(school, request.POST or None, request=request)

    On unbound forms with an ``academic_year`` field, the active year becomes the
    initial value so users don't have to choose every time. Bound forms (POST) are
    left untouched.
    """

    AY_FIELD_NAME: str = "academic_year"

    def __init__(self, *args, request=None, academic_year=None, **kwargs):
        self._injected_request = request
        self._injected_academic_year = academic_year
        super().__init__(*args, **kwargs)
        ay = academic_year
        if ay is None and request is not None:
            ay = get_active_academic_year(request)
        if ay is None:
            return
        field = self.fields.get(self.AY_FIELD_NAME)
        if field is None:
            return
        if self.is_bound and self.data.get(self.AY_FIELD_NAME):
            return
        try:
            self.initial.setdefault(self.AY_FIELD_NAME, ay.pk)
        except Exception:
            pass
