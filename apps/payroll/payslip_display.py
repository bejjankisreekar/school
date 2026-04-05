"""
Display-only helpers for payslip templates. Does not alter payroll amounts on Payslip.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.utils import timezone

_ONES = (
    "",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
)
_TEENS = (
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
)
_TENS = (
    "",
    "",
    "Twenty",
    "Thirty",
    "Forty",
    "Fifty",
    "Sixty",
    "Seventy",
    "Eighty",
    "Ninety",
)


def _below_hundred(n: int) -> str:
    if n < 10:
        return _ONES[n]
    if n < 20:
        return _TEENS[n - 10]
    t, o = divmod(n, 10)
    return _TENS[t] + (" " + _ONES[o] if o else "")


def _below_thousand(n: int) -> str:
    if n < 100:
        return _below_hundred(n)
    h, r = divmod(n, 100)
    return _ONES[h] + " Hundred" + (" And " + _below_hundred(r) if r else "")


def rupees_amount_in_words(n: Any) -> str:
    """Indian English, integer rupees, trailing 'Only' (no separate 'Rupees' — matches common slip footers)."""
    try:
        val = int(Decimal(str(n)).quantize(Decimal("1")))
    except Exception:
        return ""
    if val == 0:
        return "Zero Only"
    if val < 0:
        return "Negative " + rupees_amount_in_words(-val)

    crores, val = divmod(val, 10_000_000)
    lakhs, val = divmod(val, 100_000)
    thousands, val = divmod(val, 1000)
    rest = val

    parts: list[str] = []
    if crores:
        parts.append(_below_hundred(crores).strip() + " Crore")
    if lakhs:
        parts.append(_below_hundred(lakhs).strip() + " Lakh")
    if thousands:
        parts.append(_below_thousand(thousands).strip() + " Thousand")
    if rest:
        parts.append(_below_thousand(rest).strip())

    return " ".join(p for p in parts if p).strip() + " Only"


def _norm_breakdown(d: dict | None) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for k, v in (d or {}).items():
        try:
            out[str(k)] = Decimal(str(v))
        except Exception:
            continue
    return out


def _only_nonzero_rows(rows: list[tuple[str, Decimal]]) -> list[tuple[str, Decimal]]:
    """Drop zero-amount lines so payslips list only heads that actually apply."""
    return [(label, amt) for label, amt in rows if amt != 0]


def _take_keys(working: dict[str, Decimal], used: set[str], predicate) -> Decimal:
    total = Decimal("0")
    for k in list(working.keys()):
        if k in used:
            continue
        if predicate(k):
            total += working[k]
            used.add(k)
    return total


def _normalize_component_label(key: str) -> str:
    """Lowercase label for fuzzy matching (handles Prof./PT variants and odd spacing)."""
    s = (key or "").lower().strip()
    s = s.replace(".", " ")
    s = re.sub(r"[^\w\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _matches_professional_tax(key: str) -> bool:
    """
    Map common salary-head names to the payslip 'Professional Tax' row.
    Stored breakdown keys are SalaryComponent.name (see payroll.views._deductions_breakdown).
    """
    raw = (key or "").strip().lower()
    if raw in ("pt", "p.t", "p. t", "professional tax"):
        return True
    n = _normalize_component_label(key)
    if not n:
        return False
    if n in ("pt", "p t", "p tax", "pro tax", "prof tax", "professional tax"):
        return True
    parts = n.split()
    if parts and parts[0] == "pt":
        return True
    if "p.tax" in raw.replace(" ", "") or "ptax" in n.replace(" ", ""):
        return True
    if "professional" in n and "tax" in n:
        return True
    # Common misspelling of "Professional"
    if "proffes" in n and "tax" in n:
        return True
    if n.startswith("prof ") and "tax" in n:
        return True
    return False


def earnings_rows_for_display(payslip) -> list[tuple[str, Decimal]]:
    """Canonical earnings where they match; any remaining heads use the stored component name (no 'Other' bucket)."""
    working = _norm_breakdown(payslip.earnings_breakdown)
    used: set[str] = set()
    rows: list[tuple[str, Decimal]] = []

    basic = payslip.basic_salary
    rows.append(("Basic Salary", basic))
    for k in list(working.keys()):
        kl = k.strip().lower()
        if kl in ("basic salary", "basic"):
            used.add(k)

    rows.append(("HRA", _take_keys(working, used, lambda x: "hra" in x.lower() or "house rent" in x.lower())))
    rows.append(
        (
            "DA",
            _take_keys(
                working,
                used,
                lambda x: (de := x.lower())
                and ("dearness" in de or "d.a" in de or de.strip() == "da"),
            ),
        )
    )
    rows.append(
        (
            "Conveyance Allowance",
            _take_keys(working, used, lambda x: "conveyance" in x.lower() or "transport" in x.lower()),
        )
    )
    rows.append(("Medical Allowance", _take_keys(working, used, lambda x: "medical" in x.lower())))
    rows.append(("Special Allowance", _take_keys(working, used, lambda x: "special" in x.lower())))
    rows.append(
        ("Bonus / Incentives", _take_keys(working, used, lambda x: "bonus" in x.lower() or "incentive" in x.lower()))
    )

    for k, v in sorted(
        ((k, v) for k, v in working.items() if k not in used),
        key=lambda kv: kv[0].lower(),
    ):
        rows.append((k, v))
    return _only_nonzero_rows(rows)


def deductions_rows_for_display(payslip) -> list[tuple[str, Decimal]]:
    """Canonical deductions where they match; remaining lines keep the exact name from payroll (no 'Other' bucket)."""
    working = _norm_breakdown(payslip.deductions_breakdown)
    used: set[str] = set()
    rows: list[tuple[str, Decimal]] = []

    rows.append(("PF", _take_keys(working, used, lambda x: "pf" in x.lower() or "provident" in x.lower())))
    rows.append(("Professional Tax", _take_keys(working, used, _matches_professional_tax)))
    rows.append(("TDS", _take_keys(working, used, lambda x: "tds" in x.lower() or "tax deducted" in x.lower())))
    rows.append(("ESI", _take_keys(working, used, lambda x: "esi" in x.lower() or "e.s.i" in x.lower())))
    rows.append(
        (
            "Loan / Advance Deduction",
            _take_keys(
                working,
                used,
                lambda x: "loan" in x.lower() or "advance" in x.lower(),
            ),
        )
    )
    rows.append(
        (
            "Leave Deduction",
            _take_keys(working, used, lambda x: "leave deduction" in x.lower() or "lop" in x.lower()),
        )
    )

    for k, v in sorted(
        ((k, v) for k, v in working.items() if k not in used),
        key=lambda kv: kv[0].lower(),
    ):
        rows.append((k, v))
    return _only_nonzero_rows(rows)


_EMPTY = "-"


def _section_str(d: dict | None, *keys: str) -> str:
    if not d:
        return _EMPTY
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return _EMPTY


def _flat_str(ed: dict, *keys: str) -> str:
    return _section_str(ed, *keys)


def _bank_last_four_digits(payroll: dict, ed: dict) -> str:
    """Prefer explicit last-4 fields; else take last 4 digits from full account number."""
    for blob in (payroll, ed):
        if not blob:
            continue
        for key in ("bank_account_last4", "bank_last4", "account_last4"):
            raw = blob.get(key)
            if raw is not None and str(raw).strip():
                digits = "".join(c for c in str(raw).strip() if c.isdigit())
                if len(digits) >= 4:
                    return digits[-4:]
                if digits:
                    return digits
    for key in ("bank_account", "account_number", "bank_ac_no", "bank_ac"):
        for blob in (payroll, ed):
            if not blob:
                continue
            v = blob.get(key)
            if v is None or not str(v).strip():
                continue
            digits = "".join(c for c in str(v).strip() if c.isdigit())
            if len(digits) >= 4:
                return digits[-4:]
            if digits:
                return digits
    return _EMPTY


def teacher_payslip_meta(teacher) -> dict[str, str]:
    """
    Labels from Teacher.extra_data (nested payroll/professional blocks + legacy flat keys).
    Missing values use '-' for payslip display.
    """
    ed = teacher.extra_data or {}
    payroll = ed.get("payroll") if isinstance(ed.get("payroll"), dict) else {}
    professional = ed.get("professional") if isinstance(ed.get("professional"), dict) else {}
    basic = ed.get("basic") if isinstance(ed.get("basic"), dict) else {}

    designation = _section_str(professional, "designation") or _flat_str(ed, "designation", "job_title", "title")
    department = _section_str(professional, "department") or _flat_str(ed, "department", "dept")
    branch = _flat_str(ed, "branch", "campus", "location")
    date_of_joining = _section_str(professional, "joining_date") or _flat_str(
        ed, "date_of_joining", "doj", "joining_date"
    )
    tax_id = (
        _section_str(payroll, "pan", "tax_id", "aadhaar")
        or _section_str(basic, "id_number")
        or _flat_str(ed, "pan", "aadhaar", "tax_id", "taxid")
    )

    bank_name = _section_str(payroll, "bank_name") or _flat_str(ed, "bank_name")
    ifsc = _section_str(payroll, "ifsc") or _flat_str(ed, "ifsc", "ifsc_code")
    bank_last4 = _bank_last_four_digits(payroll, ed)

    uan = _section_str(payroll, "uan", "uan_number") or _flat_str(ed, "uan", "uan_number", "UAN")
    pf_account = (
        _section_str(payroll, "pf_account", "pf_number", "epf_number", "pf_no", "pf")
        or _flat_str(ed, "pf_account", "pf_number", "epf_number", "pf")
    )
    if pf_account == _EMPTY and uan != _EMPTY:
        pf_account = uan

    esi = _section_str(payroll, "esi", "esi_number") or _flat_str(ed, "esi", "esi_number")

    uan_pf = uan
    if pf_account != _EMPTY and pf_account != uan:
        uan_pf = f"{uan} / {pf_account}" if uan != _EMPTY else pf_account
    elif pf_account != _EMPTY:
        uan_pf = pf_account

    return {
        "designation": designation,
        "department": department,
        "branch": branch,
        "tax_id": tax_id,
        "bank_name": bank_name,
        "ifsc": ifsc,
        "bank_last4": bank_last4,
        "uan": uan,
        "pf_account": pf_account,
        "uan_pf": uan_pf,
        "esi_number": esi,
        "date_of_joining": date_of_joining,
        "working_days": _flat_str(ed, "working_days", "total_working_days"),
        "present_days": _flat_str(ed, "present_days", "days_present"),
        "paid_leave_days": _flat_str(ed, "paid_leave_days", "paid_leaves", "pl"),
        "lop_days": _flat_str(ed, "lop_days", "lop", "unpaid_leave_days"),
        "overtime_hours": _flat_str(ed, "overtime_hours", "ot_hours", "ot"),
    }


def payroll_reference(payslip) -> str:
    return f"PS-{payslip.year:04d}{payslip.month:02d}-{payslip.pk:06d}"


def school_logo_absolute_url(request, school) -> str | None:
    if not school or not getattr(school, "logo", None):
        return None
    try:
        url = school.logo.url
    except Exception:
        return None
    if request and url.startswith("/"):
        return request.build_absolute_uri(url)
    return url


def build_payslip_template_context(request, payslip, school, period_label: str, qr_data_uri: str | None) -> dict:
    teacher = payslip.teacher
    user = teacher.user
    name = user.get_full_name() or user.username
    gross = payslip.basic_salary + payslip.total_allowances
    meta = teacher_payslip_meta(teacher)
    now = timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, timezone.get_current_timezone())
    return {
        "payslip": payslip,
        "school": school,
        "period_label": period_label,
        "qr_data_uri": qr_data_uri,
        "employee_name": name,
        "employee_id_display": (teacher.employee_id or "").strip() or "—",
        "school_logo_url": school_logo_absolute_url(request, school),
        "payroll_ref": payroll_reference(payslip),
        "gross_earnings": gross,
        "net_salary_words": rupees_amount_in_words(payslip.net_salary),
        "gross_salary_words": rupees_amount_in_words(gross),
        "generated_on": now,
        "earnings_rows": earnings_rows_for_display(payslip),
        "deductions_rows": deductions_rows_for_display(payslip),
        "teacher_meta": meta,
    }


PAYSLIP_TEMPLATE_BY_FORMAT = {
    "corporate": "payroll/payslip_corporate.html",
    "classic": "payroll/payslip_classic.html",
    "minimal": "payroll/payslip_minimal.html",
}


def payslip_template_for_school(school) -> str:
    fmt = (getattr(school, "payslip_format", None) or "corporate").strip()
    return PAYSLIP_TEMPLATE_BY_FORMAT.get(fmt, PAYSLIP_TEMPLATE_BY_FORMAT["corporate"])
