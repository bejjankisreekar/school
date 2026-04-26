"""
Default MasterDataOption rows seeded for every new school tenant.

Schools may edit, reorder, add, or delete these like any other dropdown value.
Idempotent: only creates options that do not already exist (by key + name_normalized).
"""
from __future__ import annotations

import logging
from typing import Type

from django.db.models import Max

logger = logging.getLogger(__name__)

# All keys should match MasterDataOption.Key values; labels are common Indian / international school defaults.
MASTER_DATA_OPTION_DEFAULTS: dict[str, list[str]] = {
    "gender": ["Male", "Female", "Other"],
    "blood_group": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"],
    "nationality": ["Indian", "Other"],
    "religion": ["Hindu", "Muslim", "Christian", "Sikh", "Buddhist", "Jain", "Other"],
    "mother_tongue": [
        "Hindi",
        "English",
        "Tamil",
        "Telugu",
        "Bengali",
        "Marathi",
        "Gujarati",
        "Kannada",
        "Malayalam",
        "Urdu",
        "Other",
    ],
    "designation": [
        "Principal",
        "Vice Principal",
        "Head of Department",
        "Senior Teacher",
        "Teacher",
        "Lab Assistant",
        "Librarian",
        "Counsellor",
        "Administrative Staff",
        "Other",
    ],
    "department": [
        "Academics",
        "Administration",
        "Science",
        "Mathematics",
        "Languages",
        "Social Science",
        "Computer Science",
        "Sports",
        "Arts",
        "General",
    ],
    "qualification": ["B.Ed", "M.Ed", "B.Sc", "M.Sc", "M.A", "Ph.D", "Diploma", "Other"],
    "caste_category": ["General", "OBC", "SC", "ST", "EWS", "Other"],
    "marital_status": ["Single", "Married", "Divorced", "Widowed"],
    "staff_type": ["Teaching", "Non-teaching", "Administrative"],
    "employment_type": ["Permanent", "Contract", "Probation", "Visiting"],
    "shift": ["Morning", "Afternoon", "Full day", "Night"],
    "payroll_category": ["Teaching staff", "Non-teaching staff", "Administrative"],
    "experience_level": ["0-2 years", "3-5 years", "6-10 years", "10+ years"],
    "reporting_manager": ["Principal", "Vice Principal", "Head of Department", "Coordinator"],
    "relationship": ["Father", "Mother", "Guardian", "Brother", "Sister", "Grandfather", "Grandmother", "Other"],
    "occupation": ["Private sector", "Government", "Business", "Self-employed", "Homemaker", "Retired", "Other"],
    "annual_income_range": [
        "Below 3 Lakh",
        "3-5 Lakh",
        "5-10 Lakh",
        "10-20 Lakh",
        "Above 20 Lakh",
    ],
    "education_level": ["Below 10th", "10th", "12th", "Graduate", "Post-graduate", "Professional"],
    "admission_source": ["Walk-in", "Website", "Referral", "Advertisement", "Education fair", "Other"],
    "fee_category": ["General", "RTE", "Staff ward", "Sibling discount", "Merit scholarship", "Other"],
    "student_status": ["Active", "Inactive", "Graduated", "Transferred", "Alumni"],
    "previous_board": ["CBSE", "ICSE", "State Board", "IB", "IGCSE", "Other"],
    "medium_of_instruction": ["English", "Hindi", "Regional", "Bilingual"],
    "attendance_status": ["Present", "Absent", "Late", "Excused", "Holiday"],
    "admission_status": ["Pending", "Approved", "Rejected", "Waitlisted", "Enrolled"],
    "transport_required": ["Yes", "No", "Optional"],
    "status": ["Active", "Inactive"],
}


def seed_master_data_options(MasterDataOption: Type) -> int:
    """
    Insert missing default options. Returns number of rows created.

    ``MasterDataOption`` may be the live model or a migration historical model.
    """
    created = 0
    for key, names in MASTER_DATA_OPTION_DEFAULTS.items():
        existing = set(
            MasterDataOption.objects.filter(key=key).values_list("name_normalized", flat=True)
        )
        for name in names:
            raw = (name or "").strip()
            if not raw:
                continue
            nn = raw.lower()
            if nn in existing:
                continue
            next_order = (
                MasterDataOption.objects.filter(key=key).aggregate(m=Max("display_order")).get("m") or -1
            ) + 1
            MasterDataOption.objects.create(
                key=key,
                name=raw,
                name_normalized=nn,
                display_order=next_order,
                is_active=True,
            )
            existing.add(nn)
            created += 1
    return created


def ensure_master_data_defaults(school) -> int:
    """
    Run inside the given school tenant schema. Safe to call multiple times.
    Returns number of new rows created.
    """
    from django_tenants.utils import tenant_context

    from apps.school_data.models import MasterDataOption

    with tenant_context(school):
        return seed_master_data_options(MasterDataOption)
