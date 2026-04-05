from django import forms
from django.core.exceptions import ValidationError

from .models import ScheduleProfile, TimeSlot

INPUT_CLASS = "form-control"
INPUT_SM = "form-control form-control-sm"
SELECT_SM = "form-select form-select-sm"


class ScheduleProfileForm(forms.ModelForm):
    class Meta:
        model = ScheduleProfile
        fields = [
            "name",
            "description",
            "academic_year",
            "is_active",
            "default_start_time",
            "default_end_time",
            "total_periods",
            "break_enabled",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_SM, "placeholder": "e.g. Exam schedule"}),
            "description": forms.Textarea(
                attrs={"class": INPUT_SM, "rows": 2, "placeholder": "Optional notes for staff"}
            ),
            "academic_year": forms.Select(attrs={"class": SELECT_SM}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "default_start_time": forms.TimeInput(attrs={"type": "time", "class": INPUT_SM}),
            "default_end_time": forms.TimeInput(attrs={"type": "time", "class": INPUT_SM}),
            "total_periods": forms.NumberInput(attrs={"class": INPUT_SM, "min": 0, "max": 48}),
            "break_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.school_data.models import AcademicYear

        self.fields["academic_year"].queryset = AcademicYear.objects.order_by("-start_date")
        self.fields["academic_year"].required = False
        self.fields["description"].required = False
        self.fields["total_periods"].required = False
        self.fields["default_start_time"].required = False
        self.fields["default_end_time"].required = False

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise ValidationError("Enter a profile name.")
        qs = ScheduleProfile.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("A schedule profile with this name already exists.")
        return name


class TimeSlotForm(forms.ModelForm):
    """Full slot form (inline edit / update view)."""

    class Meta:
        model = TimeSlot
        fields = ["start_time", "end_time", "is_break", "break_type", "order"]
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time", "class": INPUT_SM}),
            "end_time": forms.TimeInput(attrs={"type": "time", "class": INPUT_SM}),
            "is_break": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "break_type": forms.Select(attrs={"class": SELECT_SM}),
            "order": forms.NumberInput(attrs={"class": INPUT_SM, "min": 1}),
        }

    def clean(self):
        cleaned = super().clean()
        raw = self.data.get("is_break")
        if raw is not None:
            s = str(raw).strip().lower()
            if s in ("0", "false", "off", "no", ""):
                cleaned["is_break"] = False
            elif s in ("1", "true", "on", "yes"):
                cleaned["is_break"] = True
        if not cleaned.get("is_break"):
            cleaned["break_type"] = TimeSlot.BreakType.NONE
        elif cleaned.get("break_type") == TimeSlot.BreakType.NONE:
            cleaned["break_type"] = TimeSlot.BreakType.SHORT_BREAK
        return cleaned


class TimeSlotAddForm(forms.ModelForm):
    """Add row on timeslots page — order is assigned server-side (1…n)."""

    class Meta:
        model = TimeSlot
        fields = ["start_time", "end_time", "is_break", "break_type"]
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time", "class": INPUT_SM}),
            "end_time": forms.TimeInput(attrs={"type": "time", "class": INPUT_SM}),
            "is_break": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "break_type": forms.Select(attrs={"class": SELECT_SM}),
        }

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("is_break"):
            cleaned["break_type"] = TimeSlot.BreakType.NONE
        elif cleaned.get("break_type") == TimeSlot.BreakType.NONE:
            cleaned["break_type"] = TimeSlot.BreakType.SHORT_BREAK
        return cleaned
