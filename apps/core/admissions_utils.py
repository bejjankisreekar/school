from __future__ import annotations

import random
import re
from datetime import date


_FIRST_INT_RE = re.compile(r"(\d+)")


def extract_grade_2digit(class_name: str | None) -> str:
    """
    Extract a grade number from a class label like "Grade 10" -> "10", "Class 1" -> "01".
    Falls back to "00" if none found.
    """
    s = (class_name or "").strip()
    m = _FIRST_INT_RE.search(s)
    if not m:
        return "00"
    try:
        n = int(m.group(1))
    except Exception:
        return "00"
    return f"{max(0, min(n, 99)):02d}"


def student_initials(first_name: str | None, last_name: str | None) -> str:
    """
    Lowercase initials: first letter of first + first letter of last.
    If last name missing, uses first 2 letters of first name.
    """
    fn = (first_name or "").strip()
    ln = (last_name or "").strip()
    if fn and ln:
        return (fn[0] + ln[0]).lower()
    if fn:
        return (fn[:2].ljust(2, fn[:1] or "x")).lower()
    return "xx"


def generate_admission_number(
    first_name: str,
    last_name: str,
    grade_2digit: str,
    school_code: str,
    *,
    today: date | None = None,
    rand: random.Random | None = None,
) -> str:
    """
    Format: YYSSC_RRRRRRRII
    Example: 26chs_2536486vc
    """
    d = today or date.today()
    yy = f"{d.year % 100:02d}"
    ssc = (school_code or "chs").strip().lower()
    ssc = re.sub(r"[^a-z0-9]", "", ssc)[:6] or "chs"
    ii = student_initials(first_name, last_name)

    r = rand or random.Random()
    n = r.randint(0, 9_999_999)
    rrr = f"{n:07d}"
    # grade_2digit intentionally ignored (format no longer includes class)
    return f"{yy}{ssc}_{rrr}{ii}"

