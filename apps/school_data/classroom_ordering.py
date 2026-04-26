"""
Consistent ascending sort for grade/class (ClassRoom) across the ERP.

Use these tuples in QuerySet.order_by(...) so classes appear lowest grade first.
Numeric order is stored on ClassRoom.grade_order; see grade_order_from_name() for fallback.
"""
from __future__ import annotations

import re
from typing import Final

# ClassRoom queryset: same academic year context
ORDER_GRADE_NAME: Final[tuple[str, ...]] = ("grade_order", "name")

# Typical pickers: chronological years, then grade
ORDER_AY_START_GRADE_NAME: Final[tuple[str, ...]] = (
    "academic_year__start_date",
    "grade_order",
    "name",
)
ORDER_AY_PK_GRADE_NAME: Final[tuple[str, ...]] = ("academic_year", "grade_order", "name")

# Student lists: class → section → name
ORDER_STUDENT_CLASS_SECTION: Final[tuple[str, ...]] = (
    "classroom__grade_order",
    "classroom__name",
    "section__name",
    "roll_number",
)
ORDER_STUDENT_VIA_SECTION_CLASS: Final[tuple[str, ...]] = (
    "section__classroom__grade_order",
    "section__classroom__name",
    "section__name",
    "roll_number",
)

# Fee structure grids / prefetch
ORDER_FEE_STRUCT_CLASS_TYPE: Final[tuple[str, ...]] = (
    "classroom__grade_order",
    "classroom__name",
    "academic_year__name",
    "fee_type__name",
)


_re_first_int = re.compile(r"\d+")


def grade_order_from_name(name: str) -> int:
    """
    First integer in the display name (e.g. 'Grade 10' -> 10, 'Class 1' -> 1).
    If none, returns 0 so non-numeric labels sort before numbered grades when only
    grade_order ties; ClassRoom.order_by still uses name as final tie-break.
    """
    if not name:
        return 0
    m = _re_first_int.search(str(name).strip())
    return int(m.group(0)) if m else 0
