"""Coupon validation and redemption for school subscription assignment (public schema)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import F
from django.utils import timezone

if TYPE_CHECKING:
    from .models import Coupon


def coupon_error_message(coupon: Coupon | None, *, code: str) -> str | None:
    """Return error message if coupon cannot be used, else None."""
    code = (code or "").strip()
    if not code:
        return None
    if coupon is None:
        return f"No coupon found for code “{code}”."
    if not coupon.is_active:
        return "This coupon is not active."
    today = timezone.localdate()
    if coupon.valid_from and today < coupon.valid_from:
        return "This coupon is not valid yet."
    if coupon.valid_to and today > coupon.valid_to:
        return "This coupon has expired."
    if coupon.max_usage > 0 and coupon.used_count >= coupon.max_usage:
        return "This coupon has reached its maximum number of uses."
    return None


def redeem_coupon_for_subscription(coupon: Coupon) -> None:
    """Increment usage count. Caller must wrap in transaction.atomic() and hold select_for_update on coupon if needed."""
    from .models import Coupon as CouponModel

    CouponModel.objects.filter(pk=coupon.pk).update(used_count=F("used_count") + 1)
    coupon.refresh_from_db(fields=["used_count"])
