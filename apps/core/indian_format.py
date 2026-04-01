"""Indian numbering (lakhs/crores) for currency and amounts."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def format_indian_currency(value, decimal_places: int = 2) -> str:
    """
    Format a number with Indian digit grouping.

    Examples: 1000 → 1,000; 10000 → 10,000; 100000 → 1,00,000; 1234567 → 12,34,567
    """
    if value is None or value == "":
        return "—"
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)

    negative = d < 0
    d = abs(d)
    quant = Decimal(10) ** -decimal_places
    d = d.quantize(quant, rounding=ROUND_HALF_UP)

    if decimal_places > 0:
        s = f"{d:.{decimal_places}f}"
        int_part, _, dec_part = s.partition(".")
    else:
        int_part = str(int(d))
        dec_part = ""

    if int_part == "0" or int_part.lstrip("-") == "":
        formatted_int = "0"
    elif len(int_part) <= 3:
        formatted_int = int_part
    else:
        last_three = int_part[-3:]
        rest = int_part[:-3]
        groups: list[str] = []
        while rest:
            if len(rest) <= 2:
                groups.insert(0, rest)
                break
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        formatted_int = ",".join(groups) + "," + last_three

    sign = "-" if negative else ""
    if decimal_places > 0:
        return f"{sign}{formatted_int}.{dec_part}"
    return f"{sign}{formatted_int}"
