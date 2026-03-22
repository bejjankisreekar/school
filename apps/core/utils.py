"""
Utility helpers for core app.
"""
from datetime import date


def add_warning_once(request, session_key: str, message: str):
    """
    No-op: Bootstrap alert notifications have been removed from the project.
    Kept for API compatibility with existing callers.
    """
    pass


def has_feature_access(school, feature_code: str) -> bool:
    """
    DB-driven feature check for templates and views.

    - Uses `school.get_enabled_feature_codes()` (SaaS plan + optional per-school override).
    - Strict behavior: if school has no plan/features, returns False.
    """
    if not school or not feature_code:
        return False
    try:
        codes = school.get_enabled_feature_codes()
    except Exception:
        return False
    return feature_code in (codes or set())


def get_current_academic_year() -> str:
    """
    Return academic year label in `YYYY-YYYY` format.
    Academic year starts in June.
    """
    today = date.today()
    if today.month >= 6:
        return f"{today.year}-{today.year + 1}"
    return f"{today.year - 1}-{today.year}"


def get_current_academic_year_bounds() -> tuple[date, date]:
    """
    Return (start_date, end_date) for current academic year.
    Academic year window: June 1 to May 31.
    """
    today = date.today()
    if today.month >= 6:
        start_year = today.year
    else:
        start_year = today.year - 1
    start_date = date(start_year, 6, 1)
    end_date = date(start_year + 1, 5, 31)
    return start_date, end_date
