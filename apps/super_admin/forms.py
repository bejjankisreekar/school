"""
Super Admin Control Center forms (public schema).
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Any

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import (
    CommonPasswordValidator,
    validate_password,
)
from django.contrib.auth.password_validation import get_default_password_validators
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from apps.customers.models import School

from .models import Plan


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", (s or "").strip())


def _ten_digit_in_phone_field(value: str, *, label: str) -> str:
    d = _digits_only(value)
    if len(d) != 10:
        raise ValidationError(f"{label} must be exactly 10 digits.")
    return d


def _optional_ten_digit(value: str, *, label: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    return _ten_digit_in_phone_field(raw, label=label)


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_years_clamped(d: date, years: int) -> date:
    y = d.year + years
    m = d.month
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def subscription_period_end(start: date, billing_cycle: str) -> date:
    if billing_cycle == School.SaaSBillingCycle.YEARLY:
        end_anchor = add_years_clamped(start, 1)
    else:
        end_anchor = add_months(start, 1)
    return end_anchor - timedelta(days=1)


CLASS_CHOICES = [
    ("LKG", "LKG"),
    ("UKG", "UKG"),
] + [(str(i), f"Class {i}") for i in range(1, 13)]

BOARD_CHOICES = [
    ("CBSE", "CBSE"),
    ("ICSE", "ICSE"),
    ("State", "State Board"),
    ("IB", "IB"),
]

SCHOOL_TYPE_CHOICES = [
    ("public", "Public"),
    ("private", "Private"),
    ("international", "International"),
]

MEDIUM_CHOICES = [
    ("english", "English"),
    ("telugu", "Telugu"),
    ("hindi", "Hindi"),
]

INPUT_CLASS = "form-control"


class SuperAdminCreateSchoolForm(forms.Form):
    """Full-page create school (aligned with public enrollment fields + SaaS subscription)."""

    school_name = forms.CharField(
        max_length=255,
        label="School name",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "organization"}),
    )
    society_registered_name = forms.CharField(
        required=False,
        max_length=255,
        label="Society / registered name",
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "Trust, society, or RWA registered name (optional)",
                "autocomplete": "organization",
            }
        ),
    )
    auto_generate_school_code = forms.BooleanField(
        required=False,
        initial=True,
        label="Auto-generate school code",
    )
    school_code = forms.CharField(
        max_length=6,
        required=False,
        label="School code / ID",
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "e.g. ABC123",
                "maxlength": "6",
                "autocomplete": "off",
            }
        ),
    )
    school_type = forms.ChoiceField(
        choices=SCHOOL_TYPE_CHOICES,
        label="School type",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    board = forms.ChoiceField(
        choices=BOARD_CHOICES,
        label="Board",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    established_year = forms.IntegerField(
        required=False,
        min_value=1800,
        max_value=2100,
        label="Established year",
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1800, "max": 2100}),
    )
    website = forms.CharField(
        required=False,
        max_length=500,
        label="Website URL",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "https://"}),
    )

    contact_email = forms.EmailField(
        label="School email",
        widget=forms.EmailInput(attrs={"class": INPUT_CLASS, "autocomplete": "email"}),
    )
    phone = forms.CharField(
        max_length=15,
        label="Phone number",
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASS, "inputmode": "numeric", "placeholder": "10-digit mobile"}
        ),
    )
    alternate_phone = forms.CharField(
        required=False,
        max_length=15,
        label="Alternate phone",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "inputmode": "numeric"}),
    )
    address_line1 = forms.CharField(
        max_length=255,
        label="Address line 1",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "address-line1"}),
    )
    address_line2 = forms.CharField(
        required=False,
        max_length=255,
        label="Address line 2",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "address-line2"}),
    )
    city = forms.CharField(
        max_length=120,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "address-level2"}),
    )
    state = forms.CharField(
        max_length=120,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "address-level1"}),
    )
    country = forms.CharField(
        max_length=120,
        initial="India",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "country-name"}),
    )
    pincode = forms.CharField(
        max_length=12,
        label="Pincode",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "postal-code"}),
    )

    admin_name = forms.CharField(
        max_length=255,
        label="Admin full name",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "name"}),
    )
    admin_email = forms.EmailField(
        label="Admin email",
        widget=forms.EmailInput(attrs={"class": INPUT_CLASS, "autocomplete": "email"}),
    )
    admin_phone = forms.CharField(
        max_length=15,
        label="Admin phone",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "inputmode": "numeric"}),
    )
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}),
    )

    medium = forms.ChoiceField(
        choices=MEDIUM_CHOICES,
        label="Medium of instruction",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    classes_available = forms.MultipleChoiceField(
        choices=CLASS_CHOICES,
        label="Classes available",
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": "8"}),
    )
    student_capacity = forms.IntegerField(
        required=False,
        min_value=0,
        label="Student capacity",
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
    )
    teacher_capacity = forms.IntegerField(
        required=False,
        min_value=0,
        label="Teacher capacity",
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
    )

    plan = forms.ModelChoiceField(
        queryset=Plan.objects.none(),
        label="Subscription plan",
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    billing_cycle = forms.ChoiceField(
        choices=School.SaaSBillingCycle.choices,
        label="Billing cycle",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    subscription_start_date = forms.DateField(
        label="Start date",
        widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
    )

    enrollment_notes = forms.CharField(
        required=False,
        max_length=250,
        label="Additional notes",
        widget=forms.Textarea(
            attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Onboarding notes, branches, special requests…"}
        ),
    )

    logo = forms.ImageField(
        required=False,
        label="School logo",
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
    )

    def clean_logo(self):
        f = self.cleaned_data.get("logo")
        if not f:
            return None
        size = getattr(f, "size", 0) or 0
        if size > 2 * 1024 * 1024:
            raise ValidationError("Logo must be 2 MB or smaller.")
        return f

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = Plan.objects.filter(is_active=True).order_by("name")
        self.fields["auto_generate_school_code"].widget.attrs.setdefault("class", "form-check-input")

    def clean_website(self) -> str:
        w = (self.cleaned_data.get("website") or "").strip()
        if not w:
            return ""
        if not re.match(r"^https?://", w, re.I):
            w = "https://" + w
        try:
            URLValidator()(w)
        except ValidationError as exc:
            raise ValidationError("Enter a valid website URL.") from exc
        return w[:500]

    def clean_phone(self) -> str:
        return _ten_digit_in_phone_field(self.cleaned_data.get("phone") or "", label="Phone number")

    def clean_alternate_phone(self) -> str:
        return _optional_ten_digit(self.cleaned_data.get("alternate_phone") or "", label="Alternate phone")

    def clean_admin_phone(self) -> str:
        return _ten_digit_in_phone_field(self.cleaned_data.get("admin_phone") or "", label="Admin phone")

    def clean_pincode(self) -> str:
        p = (self.cleaned_data.get("pincode") or "").strip()
        if not re.match(r"^[0-9A-Za-z\-]{3,12}$", p):
            raise ValidationError("Enter a valid pincode.")
        return p

    def clean_school_code(self) -> str:
        auto = self.cleaned_data.get("auto_generate_school_code")
        raw = (self.cleaned_data.get("school_code") or "").strip().upper()
        if auto:
            return ""
        if not raw:
            raise ValidationError("Enter a school code or enable auto-generate.")
        from apps.core.tenant_provisioning import validate_school_code_format

        try:
            code = validate_school_code_format(raw)
        except ValidationError as exc:
            raise ValidationError(
                exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            ) from None
        if School.objects.filter(code=code).exists():
            raise ValidationError("This school code is already in use.")
        return code

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        if "password1" in self.errors or "password2" in self.errors:
            return data
        p1 = data.get("password1")
        p2 = data.get("password2")
        if p1 != p2:
            self.add_error("password2", ValidationError("Passwords do not match."))
            return data
        validators = [
            v
            for v in get_default_password_validators()
            if not isinstance(v, CommonPasswordValidator)
        ]
        validate_password(p2, user=None, password_validators=validators)
        return data

    def clean_classes_available(self) -> list[str]:
        vals = self.cleaned_data.get("classes_available") or []
        if not vals:
            raise ValidationError("Select at least one class.")
        allowed = {c[0] for c in CLASS_CHOICES}
        out = [v for v in vals if v in allowed]
        if not out:
            raise ValidationError("Select at least one class.")
        return out
