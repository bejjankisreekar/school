"""
Control Center settings singleton + helpers (billing overdue reads DB when present).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.conf import settings as django_settings

from .models import ControlCenterSettings

SECRET_MASK = "••••••••••••"

DEFAULTS: dict[str, object] = {
    "platform_name": "Campus ERP",
    "default_language": "en",
    "timezone": "Asia/Kolkata",
    "default_theme": ControlCenterSettings.ThemeDefault.LIGHT,
    "default_billing_cycle": ControlCenterSettings.BillingCycleDefault.MONTHLY,
    "grace_period_days": 14,
    "gst_enabled": True,
    "gst_percent": Decimal("18"),
    "currency_code": "INR",
    "extra_charges_enabled": True,
    "concession_enabled": True,
    "email_notifications": True,
    "sms_notifications": False,
    "template_payment_reminder": "",
    "template_invoice": "",
    "template_welcome": "",
    "admin_session_timeout_minutes": 60,
    "password_min_length": 8,
    "password_require_special": False,
    "enable_two_factor": False,
    "auto_invoice_generation": False,
    "invoice_generation_day": 1,
    "auto_mark_overdue_days": 14,
    "razorpay_key_id": "",
    "razorpay_key_secret": "",
    "twilio_account_sid": "",
    "twilio_auth_token": "",
    "maintenance_mode": False,
}


def billing_auto_mark_overdue_days() -> int:
    try:
        row = ControlCenterSettings.objects.filter(pk=1).first()
        if row and row.auto_mark_overdue_days and row.auto_mark_overdue_days > 0:
            return int(row.auto_mark_overdue_days)
    except Exception:
        pass
    return int(getattr(django_settings, "BILLING_INVOICE_OVERDUE_DAYS", 14))


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "on", "yes")


def _coerce_bool(raw) -> bool:
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in ("false", "0", "no", "off", ""):
        return False
    if s in ("true", "1", "yes", "on"):
        return True
    return bool(raw)


def _is_masked(val: str) -> bool:
    s = (val or "").strip()
    return s == SECRET_MASK or (len(s) > 0 and set(s) <= {"•", "*"})


def serialize_for_api(obj: ControlCenterSettings) -> dict:
    def sec(name: str) -> str:
        cur = getattr(obj, name) or ""
        return SECRET_MASK if str(cur).strip() else ""

    return {
        "platform_name": obj.platform_name,
        "default_language": obj.default_language,
        "timezone": obj.timezone,
        "default_theme": obj.default_theme,
        "default_billing_cycle": obj.default_billing_cycle,
        "grace_period_days": obj.grace_period_days,
        "gst_enabled": obj.gst_enabled,
        "gst_percent": str(obj.gst_percent),
        "currency_code": obj.currency_code,
        "extra_charges_enabled": obj.extra_charges_enabled,
        "concession_enabled": obj.concession_enabled,
        "email_notifications": obj.email_notifications,
        "sms_notifications": obj.sms_notifications,
        "template_payment_reminder": obj.template_payment_reminder,
        "template_invoice": obj.template_invoice,
        "template_welcome": obj.template_welcome,
        "admin_session_timeout_minutes": obj.admin_session_timeout_minutes,
        "password_min_length": obj.password_min_length,
        "password_require_special": obj.password_require_special,
        "enable_two_factor": obj.enable_two_factor,
        "auto_invoice_generation": obj.auto_invoice_generation,
        "invoice_generation_day": obj.invoice_generation_day,
        "auto_mark_overdue_days": obj.auto_mark_overdue_days,
        "razorpay_key_id": obj.razorpay_key_id,
        "razorpay_key_secret": sec("razorpay_key_secret"),
        "twilio_account_sid": obj.twilio_account_sid,
        "twilio_auth_token": sec("twilio_auth_token"),
        "maintenance_mode": obj.maintenance_mode,
        "updated_at": obj.updated_at.isoformat() if obj.updated_at else None,
    }


SECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "platform": ("platform_name", "default_language", "timezone", "default_theme"),
    "billing": (
        "default_billing_cycle",
        "grace_period_days",
        "gst_enabled",
        "gst_percent",
        "currency_code",
        "extra_charges_enabled",
        "concession_enabled",
    ),
    "notifications": (
        "email_notifications",
        "sms_notifications",
        "template_payment_reminder",
        "template_invoice",
        "template_welcome",
    ),
    "security": (
        "admin_session_timeout_minutes",
        "password_min_length",
        "password_require_special",
        "enable_two_factor",
    ),
    "system": (
        "auto_invoice_generation",
        "invoice_generation_day",
        "auto_mark_overdue_days",
        "maintenance_mode",
    ),
    "integrations": ("razorpay_key_id", "razorpay_key_secret", "twilio_account_sid", "twilio_auth_token"),
}


def apply_payload(obj: ControlCenterSettings, section: str, data: dict) -> str | None:
    fields = SECTION_FIELDS.get(section)
    if not fields:
        return "Unknown section."
    update_fields: list[str] = []
    for key in fields:
        if key not in data:
            continue
        raw = data[key]
        if key in ("razorpay_key_secret", "twilio_auth_token"):
            if raw is None or _is_masked(str(raw)):
                continue
            setattr(obj, key, str(raw).strip()[:300])
            update_fields.append(key)
            continue
        if key in (
            "gst_enabled",
            "extra_charges_enabled",
            "concession_enabled",
            "email_notifications",
            "sms_notifications",
            "password_require_special",
            "enable_two_factor",
            "auto_invoice_generation",
            "maintenance_mode",
        ):
            setattr(obj, key, _coerce_bool(raw))
            update_fields.append(key)
            continue
        if key in ("grace_period_days", "admin_session_timeout_minutes"):
            try:
                v = int(raw)
            except (TypeError, ValueError):
                return f"Invalid integer: {key}"
            if v < 0 or v > 32767:
                return f"Out of range: {key}"
            setattr(obj, key, v)
            update_fields.append(key)
            continue
        if key == "password_min_length":
            try:
                v = int(raw)
            except (TypeError, ValueError):
                return "Invalid password min length."
            if v < 6 or v > 128:
                return "Password min length must be 6–128."
            setattr(obj, key, v)
            update_fields.append(key)
            continue
        if key == "invoice_generation_day":
            try:
                v = int(raw)
            except (TypeError, ValueError):
                return f"Invalid integer: {key}"
            if v < 1 or v > 28:
                return "Invoice generation day must be 1–28."
            setattr(obj, key, v)
            update_fields.append(key)
            continue
        if key == "auto_mark_overdue_days":
            try:
                v = int(raw)
            except (TypeError, ValueError):
                return f"Invalid integer: {key}"
            if v < 1 or v > 365:
                return "Auto mark overdue days must be 1–365."
            setattr(obj, key, v)
            update_fields.append(key)
            continue
        if key == "gst_percent":
            try:
                d = Decimal(str(raw))
            except (InvalidOperation, TypeError):
                return "Invalid GST percent."
            if d < 0 or d > 100:
                return "GST percent must be 0–100."
            setattr(obj, key, d)
            update_fields.append(key)
            continue
        if key == "default_billing_cycle":
            v = str(raw).strip().lower()
            if v not in {c[0] for c in ControlCenterSettings.BillingCycleDefault.choices}:
                return "Invalid billing cycle."
            setattr(obj, key, v)
            update_fields.append(key)
            continue
        if key == "default_theme":
            v = str(raw).strip().lower()
            if v not in {c[0] for c in ControlCenterSettings.ThemeDefault.choices}:
                return "Invalid theme."
            setattr(obj, key, v)
            update_fields.append(key)
            continue
        if key == "currency_code":
            setattr(obj, key, str(raw).strip().upper()[:8] or "INR")
            update_fields.append(key)
            continue
        if key == "razorpay_key_id":
            setattr(obj, key, str(raw).strip()[:120])
            update_fields.append(key)
            continue
        if key == "twilio_account_sid":
            setattr(obj, key, str(raw).strip()[:80])
            update_fields.append(key)
            continue
        if key == "platform_name":
            setattr(obj, key, str(raw).strip()[:120] or "Campus ERP")
            update_fields.append(key)
            continue
        if key in ("default_language", "timezone"):
            setattr(obj, key, str(raw).strip()[:64])
            update_fields.append(key)
            continue
        if key.startswith("template_"):
            setattr(obj, key, str(raw)[:10000])
            update_fields.append(key)
            continue
    if update_fields:
        obj.save(update_fields=list(dict.fromkeys(update_fields + ["updated_at"])))
    return None


def reset_to_defaults(obj: ControlCenterSettings) -> None:
    if obj.logo:
        obj.logo.delete(save=False)
    for k, v in DEFAULTS.items():
        setattr(obj, k, v)
    obj.logo = None
    obj.save()
