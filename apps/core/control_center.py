"""
Super Admin Control Center: tier labels, module map, helpers for platform_control_meta.
"""
from __future__ import annotations

from typing import Any

# UI product tiers → customers.Plan.name (seed_saas_plans)
CONTROL_PLAN_TIERS = (
    ("basic", "Basic", "Starter", "Up to ~300 students (configure limits below)"),
    ("standard", "Standard", "Standard", "Up to ~1,000 students"),
    ("premium", "Premium", "Enterprise", "Full modules — use as Premium tier"),
    ("enterprise", "Enterprise", "Enterprise", "Unlimited scale — full platform"),
)

TIER_TO_PLAN_NAME = {k: plan for k, _label, plan, _desc in CONTROL_PLAN_TIERS}

DURATION_CHOICES = (
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("yearly", "Yearly"),
    ("custom", "Custom"),
)

# Feature codes for school.enabled_features_override (must match Feature.code where applicable)
CONTROL_MODULE_DEFS: tuple[tuple[str, str], ...] = (
    ("students", "Student management"),
    ("teachers", "Teacher management"),
    ("attendance", "Attendance"),
    ("exams", "Exams"),
    ("fees", "Fees"),
    ("payroll", "Payroll"),
    ("timetable", "Timetable"),
    ("homework", "Homework"),
    ("reports", "Reports"),
    ("inventory", "Inventory"),
    ("library", "Library"),
    ("transport", "Transport"),
    ("hostel", "Hostel"),
    ("online_admission", "Parent portal / online admission"),
    ("online_results", "Online results"),
    ("topper_list", "Topper list"),
    ("ai_reports", "AI reports"),
    ("sms", "SMS notifications"),
    ("api_access", "Mobile app / API"),
    ("custom_branding", "Custom branding"),
    ("ai_marksheet_summaries", "AI marksheet summaries"),
    ("priority_support", "Priority support"),
)

# Role keys stored in platform_control_meta["role_permissions"] (enforcement = future phase)
CONTROL_SCHOOL_ROLES: tuple[tuple[str, str], ...] = (
    ("ADMIN", "School Admin"),
    ("TEACHER", "Teacher"),
    ("STUDENT", "Student"),
    ("PARENT", "Parent"),
    ("ACCOUNTANT", "Accountant"),
    ("HR_PAYROLL", "HR / Payroll"),
)

ROLE_PAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("dashboard", "Dashboard"),
    ("attendance", "Attendance"),
    ("marks_entry", "Marks entry"),
    ("reports", "Reports"),
    ("fees", "Fees"),
    ("payroll", "Payroll"),
    ("timetable", "Timetable"),
    ("exams", "Exams"),
    ("student_records", "Student records"),
    ("staff_records", "Staff records"),
    ("analytics", "Analytics"),
    ("settings", "School settings"),
)


def get_control_meta(school) -> dict[str, Any]:
    raw = getattr(school, "platform_control_meta", None) or {}
    return raw if isinstance(raw, dict) else {}


def merge_control_meta(school, updates: dict[str, Any]) -> None:
    meta = dict(get_control_meta(school))
    for k, v in updates.items():
        if v is None:
            meta.pop(k, None)
        else:
            meta[k] = v
    school.platform_control_meta = meta
