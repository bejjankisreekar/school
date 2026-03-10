"""
Subscription plan feature configuration.
Centralized PLAN_FEATURES and has_feature helper.
"""
from datetime import date

# Trial: 14 days, limited modules
# Basic: ₹39/student, no limit
# Pro: ₹59/student, no limit

PLAN_FEATURES = {
    "trial": [
        "students",
        "staff",
        "academics",
        "exams",
        "parent_portal",
    ],
    "basic": [
        "students",
        "fees",
        "staff",
        "academics",
        "exams",
        "parent_portal",
        "student_id_cards",
        "inventory",
        "ai_reports",
        "pdf_print",
        "secure_hosting",
        "support_24_7",
    ],
    "pro": [
        "students",
        "fees",
        "staff",
        "academics",
        "exams",
        "parent_portal",
        "student_id_cards",
        "inventory",
        "ai_reports",
        "pdf_print",
        "secure_hosting",
        "support_24_7",
        "custom_portal",
        "online_admission",
        "online_results",
        "topper_list",
        "library",
        "hostel",
        "transport",
        "api_access",
        "custom_branding",
        "ai_marksheet_summaries",
        "priority_support",
    ],
}

# Mapping from module names used in code to PLAN_FEATURES keys
MODULE_TO_FEATURE = {
    "online_admissions": "online_admission",
    "online_results": "online_results",
    "topper_list": "topper_list",
    "library": "library",
    "hostel": "hostel",
    "transport": "transport",
    "api_access": "api_access",
    "custom_branding": "custom_branding",
    "fees": "fees",
}


def has_feature(school, feature: str) -> bool:
    """
    Check if school's plan includes the given feature.
    Returns True if school has no plan (legacy/full access) or plan includes feature.
    """
    if not school:
        return False
    plan = getattr(school, "plan", None)
    if not plan:
        # Fallback: use subscription_plan for backward compat
        old_plan = getattr(school, "subscription_plan", None)
        if old_plan:
            # Map old plan types
            pt = getattr(old_plan, "plan_type", "") or ""
            plan_name = "pro" if pt in ("PRO", "ENTERPRISE") else "basic"
        else:
            return True  # No plan = full access for backward compat
    else:
        plan_name = (getattr(plan, "name", None) or "").lower()
    feature = MODULE_TO_FEATURE.get(feature, feature)
    return feature in PLAN_FEATURES.get(plan_name, [])


def is_trial_expired(school) -> bool:
    """Check if trial has expired."""
    if not school:
        return False
    plan = getattr(school, "plan", None)
    if not plan:
        return False
    plan_name = (getattr(plan, "name", None) or "").lower()
    if plan_name != "trial":
        return False
    end = getattr(school, "trial_end_date", None)
    if not end:
        return False
    return date.today() > end
