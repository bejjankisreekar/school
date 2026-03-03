from django import forms
from .models import TimeSlot

INPUT_CLASS = "form-control"


class TimeSlotForm(forms.ModelForm):
    class Meta:
        model = TimeSlot
        fields = ["start_time", "end_time", "is_break", "break_type", "order"]
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time", "class": INPUT_CLASS}),
            "end_time": forms.TimeInput(attrs={"type": "time", "class": INPUT_CLASS}),
            "is_break": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "break_type": forms.Select(attrs={"class": INPUT_CLASS}),
            "order": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
        }
