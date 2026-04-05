from decimal import Decimal

from django import forms
from django.db.models import Max

from .models import SalaryComponent, SalaryStructure, SalaryAdvance

INPUT_CLASS = "form-control form-control-lg rounded-3"
BS_INPUT = "form-control form-select form-select-lg rounded-3"
INPUT_SM = "form-control rounded-3"


class SalaryComponentForm(forms.ModelForm):
    """Payroll salary component; includes optional preview-only sample basic for UI."""

    example_basic = forms.DecimalField(
        label="Sample basic salary (preview only)",
        required=False,
        min_value=Decimal("0"),
        max_digits=12,
        decimal_places=2,
        initial=Decimal("50000"),
        help_text="Used only below to show how this component affects pay. Not saved.",
        widget=forms.NumberInput(
            attrs={"class": INPUT_SM, "step": "0.01", "min": 0, "id": "id_example_basic"}
        ),
    )

    class Meta:
        model = SalaryComponent
        fields = [
            "name",
            "code",
            "description",
            "component_type",
            "calculation_type",
            "value",
            "order",
            "is_active",
        ]
        labels = {
            "name": "Component name",
            "code": "Report code",
            "description": "Description & notes",
            "component_type": "Component category",
            "calculation_type": "How the amount is calculated",
            "value": "Amount or percentage",
            "order": "Display order",
            "is_active": "Active on payroll",
        }
        help_texts = {
            "name": "Shown on payslips and reports (e.g. House Rent Allowance).",
            "code": "Optional short code for exports and integrations.",
            "description": "Optional: policy text, GL hints, or who qualifies.",
            "component_type": "Allowances increase gross pay; deductions reduce net pay.",
            "calculation_type": "Percentage applies to employee basic salary; fixed is the same rupee amount for everyone.",
            "value": "Enter a percentage (0–100) or a fixed rupee amount, based on calculation type above.",
            "order": "Lower numbers appear first when listing components (0 = first).",
            "is_active": "Inactive components are ignored when payroll is generated.",
        }
        widgets = {
            "name": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "e.g. House Rent Allowance", "autocomplete": "off"}
            ),
            "code": forms.TextInput(
                attrs={"class": INPUT_SM, "placeholder": "e.g. HRA", "autocomplete": "off", "maxlength": "40"}
            ),
            "description": forms.Textarea(
                attrs={"class": INPUT_SM, "rows": 3, "placeholder": "Optional internal notes…"}
            ),
            "component_type": forms.Select(attrs={"class": BS_INPUT, "id": "id_component_type"}),
            "calculation_type": forms.Select(attrs={"class": BS_INPUT, "id": "id_calculation_type"}),
            "value": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "step": "0.01", "min": 0, "id": "id_value"}
            ),
            "order": forms.NumberInput(attrs={"class": INPUT_SM, "min": 1, "id": "id_order"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        order_f = self.fields["order"]
        order_f.widget.attrs.setdefault("min", 1)

        if not (self.instance and self.instance.pk):
            mx = SalaryComponent.objects.aggregate(m=Max("order"))["m"]
            next_order = (mx or 0) + 1
            if next_order < 1:
                next_order = 1
            order_f.initial = next_order
            if not self.data:
                self.initial["order"] = next_order

        if self.instance and self.instance.pk:
            self.fields["example_basic"].widget.attrs.setdefault(
                "placeholder", "Adjust to preview this component"
            )

    def clean_order(self):
        order = self.cleaned_data.get("order")
        if order is not None and order < 1:
            raise forms.ValidationError("Display order must be 1 or higher.")
        return order


class SalaryStructureForm(forms.ModelForm):
    class Meta:
        model = SalaryStructure
        fields = ["teacher", "designation", "department", "basic_salary"]
        widgets = {
            "teacher": forms.Select(attrs={"class": "form-select form-select-lg rounded-3"}),
            "designation": forms.TextInput(
                attrs={"class": "form-control form-control-lg rounded-3", "placeholder": "e.g. Senior Teacher", "autocomplete": "off"}
            ),
            "department": forms.TextInput(
                attrs={"class": "form-control form-control-lg rounded-3", "placeholder": "e.g. Academics", "autocomplete": "off"}
            ),
            "basic_salary": forms.NumberInput(
                attrs={"class": "form-control form-control-lg rounded-3", "step": "0.01", "min": 0, "placeholder": "0.00"}
            ),
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
