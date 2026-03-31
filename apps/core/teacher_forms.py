"""School admin: teacher master profile (parity with extended student fields)."""

from django import forms
from django.contrib.auth import get_user_model

from apps.school_data.models import ClassRoom, Subject, Teacher

from .forms import BS_INPUT, INPUT_CLASS

User = get_user_model()


class TeacherMasterForm(forms.Form):
    """Create / edit teacher with extra_data JSON sections (contact, professional, family, medical, payroll, status)."""

    # —— Account ——
    username = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Login username"}),
    )
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "placeholder": "Leave blank to keep current"}),
    )

    # —— Identity ——
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    middle_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    last_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={"class": INPUT_CLASS}))
    employee_id = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    phone_number = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    qualification = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    experience = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
    )
    gender = forms.ChoiceField(
        choices=[("", "— Not specified —")] + list(Teacher.Gender.choices),
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )
    blood_group = forms.ChoiceField(
        choices=[
            ("", "— Not specified —"),
            ("A+", "A+"),
            ("A-", "A-"),
            ("B+", "B+"),
            ("B-", "B-"),
            ("AB+", "AB+"),
            ("AB-", "AB-"),
            ("O+", "O+"),
            ("O-", "O-"),
        ],
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )
    id_number = forms.CharField(
        max_length=50,
        required=False,
        label="Aadhar / ID number",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS}),
    )
    nationality = forms.CharField(max_length=60, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    religion = forms.CharField(max_length=60, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    mother_tongue = forms.CharField(max_length=60, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    profile_image = forms.ImageField(
        required=False,
        label="Photo",
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )
    role = forms.ChoiceField(choices=User.Roles.choices, widget=forms.Select(attrs={"class": BS_INPUT}))

    subjects = forms.ModelMultipleChoiceField(
        queryset=Subject.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": BS_INPUT, "id": "id_teacher_subjects"}),
    )
    classrooms = forms.ModelMultipleChoiceField(
        queryset=ClassRoom.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": BS_INPUT, "id": "id_teacher_classrooms"}),
    )

    address_line1 = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    address_line2 = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    city = forms.CharField(max_length=80, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    district = forms.CharField(max_length=80, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    state = forms.CharField(max_length=80, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    pincode = forms.CharField(max_length=12, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    country = forms.CharField(
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "India"}),
    )

    joining_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}))
    designation = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    department = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))

    spouse_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    spouse_phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    emergency_contact_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    emergency_phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))

    allergies = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    medical_conditions = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    doctor_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    hospital = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    insurance_details = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))

    bank_name = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    bank_account = forms.CharField(max_length=40, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    ifsc = forms.CharField(max_length=20, required=False, label="IFSC", widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    uan_number = forms.CharField(max_length=20, required=False, label="UAN / PF", widget=forms.TextInput(attrs={"class": INPUT_CLASS}))

    record_status = forms.ChoiceField(
        choices=[
            ("ACTIVE", "Active"),
            ("INACTIVE", "Inactive"),
            ("ON_LEAVE", "On leave"),
            ("EXITED", "Exited"),
        ],
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT}),
        initial="ACTIVE",
    )

    def __init__(self, school, teacher=None, *args, **kwargs):
        self.school = school
        self.teacher = teacher
        super().__init__(*args, **kwargs)
        self.fields["subjects"].queryset = Subject.objects.order_by("name")
        self.fields["classrooms"].queryset = ClassRoom.objects.select_related("academic_year").order_by(
            "academic_year__start_date", "name"
        )

        ph = {
            "first_name": "e.g. Priya",
            "middle_name": "Optional",
            "last_name": "e.g. Sharma",
            "email": "name@school.edu",
            "employee_id": "e.g. TCH-1024",
            "phone_number": "10-digit mobile",
            "qualification": "e.g. M.Ed, B.Sc",
            "experience": "e.g. 5 years",
            "designation": "e.g. Senior teacher",
            "department": "e.g. Sciences",
            "id_number": "Government ID / Aadhar",
            "nationality": "e.g. Indian",
            "religion": "Optional",
            "mother_tongue": "e.g. Hindi",
            "address_line1": "Street, building",
            "address_line2": "Area, landmark",
            "city": "City",
            "district": "District",
            "state": "State",
            "pincode": "PIN",
            "spouse_name": "Full name",
            "spouse_phone": "Phone",
            "emergency_contact_name": "Name",
            "emergency_phone": "Phone",
            "allergies": "If any",
            "medical_conditions": "If any",
            "doctor_name": "Doctor name",
            "hospital": "Hospital / clinic",
            "insurance_details": "Policy / number",
            "bank_name": "Bank name",
            "bank_account": "Account number",
            "ifsc": "IFSC code",
            "uan_number": "UAN or PF number",
        }
        for fname, text in ph.items():
            if fname in self.fields:
                self.fields[fname].widget.attrs.setdefault("placeholder", text)

        if teacher:
            self.fields["username"].widget.attrs["readonly"] = True
            self.fields["username"].disabled = True
            self.fields["password"].help_text = "Leave blank to keep the current password."
            extra = teacher.extra_data or {}
            basic = extra.get("basic") or {}
            contact = extra.get("contact") or {}
            prof = extra.get("professional") or {}
            family = extra.get("family") or {}
            medical = extra.get("medical") or {}
            payroll = extra.get("payroll") or {}
            status_block = extra.get("status") or {}
            addr_lines = (teacher.address or "").split("\n") if teacher.address else []
            self.initial.setdefault("first_name", teacher.user.first_name)
            self.initial.setdefault("last_name", teacher.user.last_name)
            self.initial.setdefault("middle_name", basic.get("middle_name") or "")
            self.initial.setdefault("email", teacher.user.email or "")
            self.initial.setdefault("username", teacher.user.username)
            self.initial.setdefault("employee_id", teacher.employee_id or "")
            self.initial.setdefault("phone_number", teacher.phone_number or "")
            self.initial.setdefault("qualification", teacher.qualification or "")
            self.initial.setdefault("experience", teacher.experience or "")
            self.initial.setdefault("date_of_birth", teacher.date_of_birth)
            self.initial.setdefault("gender", teacher.gender or "")
            self.initial.setdefault("blood_group", basic.get("blood_group") or "")
            self.initial.setdefault("id_number", basic.get("id_number") or "")
            self.initial.setdefault("nationality", basic.get("nationality") or "")
            self.initial.setdefault("religion", basic.get("religion") or "")
            self.initial.setdefault("mother_tongue", basic.get("mother_tongue") or "")
            self.initial.setdefault("role", teacher.user.role)
            self.initial.setdefault("subjects", list(teacher.subjects.all()))
            self.initial.setdefault("classrooms", list(teacher.classrooms.all()))
            self.initial.setdefault("address_line1", addr_lines[0] if addr_lines else "")
            self.initial.setdefault("address_line2", addr_lines[1] if len(addr_lines) > 1 else "")
            self.initial.setdefault("city", contact.get("city") or "")
            self.initial.setdefault("district", contact.get("district") or "")
            self.initial.setdefault("state", contact.get("state") or "")
            self.initial.setdefault("pincode", contact.get("pincode") or "")
            self.initial.setdefault("country", contact.get("country") or "")
            self.initial.setdefault("joining_date", prof.get("joining_date") or None)
            self.initial.setdefault("designation", prof.get("designation") or "")
            self.initial.setdefault("department", prof.get("department") or "")
            self.initial.setdefault("spouse_name", family.get("spouse_name") or "")
            self.initial.setdefault("spouse_phone", family.get("spouse_phone") or "")
            self.initial.setdefault(
                "emergency_contact_name",
                medical.get("emergency_contact_name") or family.get("emergency_contact_name") or "",
            )
            self.initial.setdefault(
                "emergency_phone",
                medical.get("emergency_phone") or family.get("emergency_phone") or "",
            )
            self.initial.setdefault("allergies", medical.get("allergies") or "")
            self.initial.setdefault("medical_conditions", medical.get("medical_conditions") or "")
            self.initial.setdefault("doctor_name", medical.get("doctor_name") or "")
            self.initial.setdefault("hospital", medical.get("hospital") or "")
            self.initial.setdefault("insurance_details", medical.get("insurance_details") or "")
            self.initial.setdefault("bank_name", payroll.get("bank_name") or "")
            self.initial.setdefault("bank_account", payroll.get("bank_account") or "")
            self.initial.setdefault("ifsc", payroll.get("ifsc") or "")
            self.initial.setdefault("uan_number", payroll.get("uan_number") or "")
            self.initial.setdefault(
                "record_status",
                status_block.get("record_status") or ("ACTIVE" if teacher.user.is_active else "INACTIVE"),
            )
        else:
            del self.fields["role"]
            self.fields["password"].required = True
            self.fields["username"].required = True

    def clean_username(self):
        u = (self.cleaned_data.get("username") or "").strip()
        if self.teacher:
            return self.teacher.user.username
        if not u:
            raise forms.ValidationError("Username is required.")
        if User.objects.filter(username=u).exists():
            raise forms.ValidationError("Username already exists.")
        return u

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if not email:
            return email
        qs = User.objects.filter(email__iexact=email)
        if self.teacher and self.teacher.user_id:
            qs = qs.exclude(pk=self.teacher.user_id)
        if qs.exists():
            raise forms.ValidationError("This email is already in use.")
        return email

    def clean_employee_id(self):
        eid = (self.cleaned_data.get("employee_id") or "").strip()
        if not eid:
            return ""
        qs = Teacher.objects.filter(employee_id=eid)
        if self.teacher:
            qs = qs.exclude(pk=self.teacher.pk)
        if qs.exists():
            raise forms.ValidationError("Employee ID already exists.")
        return eid

    def clean(self):
        data = super().clean()
        if not self.teacher:
            if not (data.get("password") or "").strip():
                self.add_error("password", "Password is required for new teachers.")
        return data

    @staticmethod
    def build_extra_data(data):
        joining = data.get("joining_date")
        return {
            "basic": {
                "middle_name": (data.get("middle_name") or "").strip(),
                "blood_group": data.get("blood_group") or "",
                "id_number": data.get("id_number") or "",
                "nationality": data.get("nationality") or "",
                "religion": data.get("religion") or "",
                "mother_tongue": data.get("mother_tongue") or "",
            },
            "contact": {
                "city": data.get("city") or "",
                "district": data.get("district") or "",
                "state": data.get("state") or "",
                "pincode": data.get("pincode") or "",
                "country": data.get("country") or "",
            },
            "professional": {
                "joining_date": str(joining) if joining else "",
                "designation": data.get("designation") or "",
                "department": data.get("department") or "",
            },
            "family": {
                "spouse_name": data.get("spouse_name") or "",
                "spouse_phone": data.get("spouse_phone") or "",
            },
            "medical": {
                "emergency_contact_name": data.get("emergency_contact_name") or "",
                "emergency_phone": data.get("emergency_phone") or "",
                "allergies": data.get("allergies") or "",
                "medical_conditions": data.get("medical_conditions") or "",
                "doctor_name": data.get("doctor_name") or "",
                "hospital": data.get("hospital") or "",
                "insurance_details": data.get("insurance_details") or "",
            },
            "payroll": {
                "bank_name": data.get("bank_name") or "",
                "bank_account": data.get("bank_account") or "",
                "ifsc": data.get("ifsc") or "",
                "uan_number": data.get("uan_number") or "",
            },
            "status": {
                "record_status": (data.get("record_status") or "ACTIVE").strip().upper(),
            },
        }
