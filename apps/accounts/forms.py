from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from apps.customers.models import School

from .models import UserProfile

User = get_user_model()


class UserAccountCoreForm(forms.ModelForm):
    """Editable core identity fields stored on User."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone_number")
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "last_name": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "email": forms.EmailInput(attrs={"class": "form-control rounded-3"}),
            "phone_number": forms.TextInput(attrs={"class": "form-control rounded-3", "placeholder": "Primary mobile"}),
        }


class UserProfilePersonalForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = (
            "middle_name",
            "display_name",
            "date_of_birth",
            "gender",
            "marital_status",
            "blood_group",
            "nationality",
            "aadhaar_or_govt_id",
            "emergency_contact_name",
            "emergency_contact_phone",
        )
        widgets = {
            "middle_name": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "display_name": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "date_of_birth": forms.DateInput(attrs={"class": "form-control rounded-3", "type": "date"}),
            "gender": forms.Select(attrs={"class": "form-select rounded-3"}),
            "marital_status": forms.Select(attrs={"class": "form-select rounded-3"}),
            "blood_group": forms.Select(attrs={"class": "form-select rounded-3"}),
            "nationality": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "aadhaar_or_govt_id": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "emergency_contact_name": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "emergency_contact_phone": forms.TextInput(attrs={"class": "form-control rounded-3"}),
        }


class UserProfileContactForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = (
            "secondary_email",
            "alternate_phone",
            "whatsapp_number",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "pin_code",
            "country",
        )
        widgets = {
            "secondary_email": forms.EmailInput(attrs={"class": "form-control rounded-3"}),
            "alternate_phone": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "whatsapp_number": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "address_line1": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "address_line2": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "city": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "state": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "pin_code": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "country": forms.TextInput(attrs={"class": "form-control rounded-3"}),
        }


class UserProfileOrganizationForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = (
            "department",
            "designation",
            "branch",
            "reporting_manager",
            "employee_code",
            "official_email",
        )
        widgets = {
            "department": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "designation": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "branch": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "reporting_manager": forms.Select(attrs={"class": "form-select rounded-3"}),
            "employee_code": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "official_email": forms.EmailInput(attrs={"class": "form-control rounded-3"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None and "reporting_manager" in self.fields:
            qs = User.objects.filter(is_active=True).exclude(pk=user.pk).order_by("first_name", "last_name", "username")
            if user.school_id:
                qs = qs.filter(Q(school=user.school) | Q(role=User.Roles.SUPERADMIN))
            self.fields["reporting_manager"].queryset = qs
            self.fields["reporting_manager"].required = False


class UserProfilePreferencesForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("language", "timezone", "theme", "notify_in_app", "notify_email", "notify_sms")
        widgets = {
            "language": forms.TextInput(attrs={"class": "form-control rounded-3", "placeholder": "e.g. en"}),
            "timezone": forms.TextInput(attrs={"class": "form-control rounded-3", "placeholder": "e.g. Asia/Kolkata"}),
            "theme": forms.Select(attrs={"class": "form-select rounded-3"}),
            "notify_in_app": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notify_email": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notify_sms": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class UserProfileSecurityPrefsForm(forms.ModelForm):
    """Security-related toggles the user may edit (2FA placeholder until wired)."""

    class Meta:
        model = UserProfile
        fields = ("two_factor_enabled",)
        widgets = {
            "two_factor_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class UserAvatarForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("avatar",)
        widgets = {
            "avatar": forms.FileInput(
                attrs={
                    "class": "form-control form-control-sm rounded-3",
                    "accept": "image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp",
                }
            ),
        }


class SchoolInstitutionProfileForm(forms.ModelForm):
    """Tenant school record — editable on account profile by school admin only."""

    class Meta:
        model = School
        fields = (
            "name",
            "institution_type",
            "date_of_establishment",
            "board_affiliation",
            "registration_number",
            "website",
            "contact_person",
            "contact_email",
            "phone",
            "address",
            "header_text",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "institution_type": forms.Select(attrs={"class": "form-select rounded-3"}),
            "date_of_establishment": forms.DateInput(attrs={"class": "form-control rounded-3", "type": "date"}),
            "board_affiliation": forms.TextInput(attrs={"class": "form-control rounded-3", "placeholder": "e.g. CBSE, State Board"}),
            "registration_number": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "website": forms.URLInput(attrs={"class": "form-control rounded-3", "placeholder": "https://"}),
            "contact_person": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "contact_email": forms.EmailInput(attrs={"class": "form-control rounded-3"}),
            "phone": forms.TextInput(attrs={"class": "form-control rounded-3"}),
            "address": forms.Textarea(attrs={"class": "form-control rounded-3", "rows": 3}),
            "header_text": forms.TextInput(attrs={"class": "form-control rounded-3"}),
        }
