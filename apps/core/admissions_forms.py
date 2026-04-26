from __future__ import annotations

from datetime import date

from django import forms
from django.core.exceptions import ValidationError

from apps.core.forms import BS_INPUT, INPUT_CLASS
from apps.school_data.classroom_ordering import ORDER_GRADE_NAME
from apps.school_data.models import Admission, AdmissionDocument, ClassRoom, MasterDataOption


def _validate_10_digit_mobile(v: str) -> str:
    s = "".join(ch for ch in (v or "").strip() if ch.isdigit())
    if not s:
        return ""
    if len(s) != 10:
        raise ValidationError("Mobile number must be 10 digits.")
    return s


class AdmissionForm(forms.ModelForm):
    gender = forms.ChoiceField(
        choices=[("", "— Not specified —")],
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )
    blood_group = forms.ChoiceField(
        choices=[("", "— Not specified —")],
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )
    transfer_certificate = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"}))
    birth_certificate = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"}))

    class Meta:
        model = Admission
        fields = [
            # Student
            "first_name",
            "last_name",
            "gender",
            "date_of_birth",
            "blood_group",
            "aadhaar_or_student_id",
            "passport_photo",
            # Parent
            "father_name",
            "mother_name",
            "mobile_number",
            "alternate_mobile",
            "email",
            "occupation",
            "annual_income",
            # Address
            "house_no",
            "street",
            "city",
            "state",
            "pincode",
            # Academic
            "applying_for_class",
            "previous_school_name",
            "previous_marks_percent",
            # Transport
            "require_bus",
            "pickup_point",
            # Other
            "admission_date",
            "status",
            "notes",
        ]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "First name"}),
            "last_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Last name"}),
            "date_of_birth": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "aadhaar_or_student_id": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Aadhaar / Student ID"}),
            "passport_photo": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "father_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "mother_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "mobile_number": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "10-digit mobile"}),
            "alternate_mobile": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Optional"}),
            "email": forms.EmailInput(attrs={"class": INPUT_CLASS, "placeholder": "Optional"}),
            "occupation": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Optional"}),
            "annual_income": forms.NumberInput(attrs={"class": INPUT_CLASS, "placeholder": "Optional"}),
            "house_no": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "House no"}),
            "street": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Street"}),
            "city": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "state": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "pincode": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "PIN"}),
            "applying_for_class": forms.Select(attrs={"class": BS_INPUT}),
            "previous_school_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Optional"}),
            "previous_marks_percent": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Optional"}),
            "require_bus": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "pickup_point": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Pickup point"}),
            "admission_date": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "status": forms.Select(attrs={"class": BS_INPUT}),
            "notes": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": "Internal notes"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Applying class choices are tenant-scoped
        self.fields["applying_for_class"].queryset = ClassRoom.objects.order_by(*ORDER_GRADE_NAME)
        self.fields["first_name"].required = True
        self.fields["mobile_number"].required = True

        def _master_choices(master_key: str, empty_label: str = "— Not specified —"):
            qs = MasterDataOption.objects.filter(key=master_key, is_active=True).order_by("name")
            return [("", empty_label)] + [(o.name, o.name) for o in qs.only("name")]

        self.fields["gender"].choices = _master_choices("gender", "— Not specified —")
        self.fields["blood_group"].choices = _master_choices("blood_group", "— Not specified —")

    def clean_mobile_number(self):
        v = self.cleaned_data.get("mobile_number") or ""
        return _validate_10_digit_mobile(v)

    def clean_alternate_mobile(self):
        v = self.cleaned_data.get("alternate_mobile") or ""
        return _validate_10_digit_mobile(v)

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get("date_of_birth")
        if dob and dob > date.today():
            raise ValidationError("DOB cannot be a future date.")
        return dob

    def clean_aadhaar_or_student_id(self):
        v = (self.cleaned_data.get("aadhaar_or_student_id") or "").strip()
        if not v:
            return ""
        qs = Admission.objects.filter(aadhaar_or_student_id=v)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("Duplicate Aadhaar / Student ID already exists in admissions.")
        return v

    def clean(self):
        data = super().clean()
        mobile = data.get("mobile_number") or ""
        if mobile:
            qs = Admission.objects.filter(mobile_number=mobile)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("mobile_number", "Duplicate mobile number already exists in admissions.")
        return data

    def save(self, commit=True, user=None):
        inst: Admission = super().save(commit=False)
        if commit:
            if user:
                inst.save_with_audit(user)
            else:
                inst.save()
            self._save_documents(inst)
        return inst

    def _save_documents(self, inst: Admission) -> None:
        tc = self.files.get("transfer_certificate")
        bc = self.files.get("birth_certificate")
        if tc:
            AdmissionDocument.objects.create(
                admission=inst,
                doc_type=AdmissionDocument.DocType.TRANSFER_CERT,
                title="Transfer Certificate",
                file=tc,
            )
        if bc:
            AdmissionDocument.objects.create(
                admission=inst,
                doc_type=AdmissionDocument.DocType.BIRTH_CERT,
                title="Birth Certificate",
                file=bc,
            )

