from django import forms
from .models import SalaryComponent, SalaryStructure, SalaryAdvance

INPUT_CLASS = "form-control"
BS_INPUT = "form-control form-select"


class SalaryComponentForm(forms.ModelForm):
    class Meta:
        model = SalaryComponent
        fields = ["name", "component_type", "calculation_type", "value", "is_active", "order"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "component_type": forms.Select(attrs={"class": BS_INPUT}),
            "calculation_type": forms.Select(attrs={"class": BS_INPUT}),
            "value": forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "0.01", "min": 0}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "order": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
        }


class SalaryStructureForm(forms.ModelForm):
    class Meta:
        model = SalaryStructure
        fields = ["teacher", "designation", "department", "basic_salary"]
        widgets = {
            "teacher": forms.Select(attrs={"class": BS_INPUT}),
            "designation": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "department": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "basic_salary": forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "0.01", "min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.school_data.models import Teacher
        self.fields["teacher"].queryset = Teacher.objects.select_related("user").order_by("user__first_name", "user__last_name")


class SalaryAdvanceForm(forms.ModelForm):
    class Meta:
        model = SalaryAdvance
        fields = ["teacher", "amount", "advance_date", "monthly_deduction", "reason", "status"]
        widgets = {
            "teacher": forms.Select(attrs={"class": BS_INPUT}),
            "amount": forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "0.01", "min": 0}),
            "advance_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "monthly_deduction": forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "0.01", "min": 0}),
            "reason": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "status": forms.Select(attrs={"class": BS_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.school_data.models import Teacher
        self.fields["teacher"].queryset = Teacher.objects.select_related("user").order_by("user__first_name", "user__last_name")
