INPUT_CLASS = "form-control"
BS_INPUT = "form-control form-select"  # for selects

from django import forms
from django.contrib.auth import get_user_model
from apps.customers.models import School, SubscriptionPlan, Plan
from apps.school_data.models import (
    Homework,
    Marks,
    Attendance,
    Exam,
    Student,
    Teacher,
    Section,
    ClassRoom,
    Subject,
    AcademicYear,
    FeeType,
    FeeStructure,
    Fee,
    Payment,
    StaffAttendance,
    SupportTicket,
    InventoryItem,
    Purchase,
    Invoice,
    InvoiceItem,
    Book,
    BookIssue,
    Hostel,
    HostelRoom,
    HostelAllocation,
    HostelFee,
    Route,
    Vehicle,
    Driver,
    StudentRouteAssignment,
    OnlineAdmission,
)
from .models import ContactEnquiry

User = get_user_model()


class HomeworkForm(forms.ModelForm):
    class Meta:
        model = Homework
        fields = ["subject", "title", "description", "due_date"]
        widgets = {
            "subject": forms.Select(attrs={"class": INPUT_CLASS}),
            "title": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Homework title"}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": "Description"}),
            "due_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
        }


class HomeworkCreateForm(forms.ModelForm):
    """Create homework with class+section assignment. Role-based filtering in __init__."""
    class Meta:
        model = Homework
        fields = ["title", "description", "classes", "sections", "due_date"]
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Homework title"}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 4, "placeholder": "Description"}),
            "classes": forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
            "sections": forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
            "due_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.school_data.models import ClassSectionSubjectTeacher
        self.fields["classes"].required = True
        self.fields["sections"].required = True

        if user and getattr(user, "role", None) == "ADMIN":
            self.fields["classes"].queryset = ClassRoom.objects.select_related("academic_year").order_by("academic_year__start_date", "name")
            self.fields["sections"].queryset = Section.objects.order_by("name")
        elif user and getattr(user, "role", None) == "TEACHER":
            teacher = getattr(user, "teacher_profile", None)
            if teacher:
                mappings = ClassSectionSubjectTeacher.objects.filter(teacher=teacher).select_related("class_obj", "section")
                class_ids = list(mappings.values_list("class_obj_id", flat=True).distinct())
                section_ids = list(mappings.values_list("section_id", flat=True).distinct())
                self.fields["classes"].queryset = ClassRoom.objects.filter(id__in=class_ids).order_by("name")
                self.fields["sections"].queryset = Section.objects.filter(id__in=section_ids).order_by("name")
            else:
                self.fields["classes"].queryset = ClassRoom.objects.none()
                self.fields["sections"].queryset = Section.objects.none()
        else:
            self.fields["classes"].queryset = ClassRoom.objects.order_by("name")
            self.fields["sections"].queryset = Section.objects.order_by("name")


class TeacherHomeworkForm(forms.ModelForm):
    """Legacy: homework with subject. Kept for backward compat."""
    class Meta:
        model = Homework
        fields = ["title", "description", "due_date"]
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Homework title"}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": "Description"}),
            "due_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
        }


class MarksForm(forms.ModelForm):
    class Meta:
        model = Marks
        fields = ["student", "subject", "exam_name", "exam_date", "marks_obtained", "total_marks"]
        widgets = {
            "student": forms.Select(attrs={"class": INPUT_CLASS}),
            "subject": forms.Select(attrs={"class": INPUT_CLASS}),
            "exam_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Mid-term Exam"}),
            "exam_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "marks_obtained": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
            "total_marks": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1}),
        }


class ExamCreateForm(forms.ModelForm):
    class Meta:
        model = Exam
        fields = ["name", "class_name", "section", "date"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Mid Term, Final Exam"}),
            "class_name": forms.Select(attrs={"class": INPUT_CLASS}),
            "section": forms.Select(attrs={"class": INPUT_CLASS}),
            "date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
        }

    def __init__(self, *args, allowed_pairs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_pairs:
            # Restrict to teacher's mapped class+section pairs.
            class_names = sorted({c for c, _ in allowed_pairs if c})
            section_names = sorted({s for _, s in allowed_pairs if s})
            self.fields["class_name"].choices = [("", "---------")] + [(n, n) for n in class_names]
            self.fields["section"].choices = [("", "---------")] + [(n, n) for n in section_names]
        else:
            self.fields["class_name"].choices = [(c.name, c.name) for c in ClassRoom.objects.order_by("name")]
            self.fields["section"].choices = [(s.name, s.name) for s in Section.objects.order_by("name")]


class ContactEnquiryForm(forms.ModelForm):
    """
    Validation for the public /contact/ form.
    Enforces required fields and keeps message length under control.
    """

    message = forms.CharField(
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 4}),
        max_length=1000,
    )

    class Meta:
        model = ContactEnquiry
        fields = ["name", "email", "phone", "school_name", "message"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "email": forms.EmailInput(attrs={"class": INPUT_CLASS}),
            "phone": forms.TextInput(attrs={"class": INPUT_CLASS, "required": False}),
            "school_name": forms.TextInput(attrs={"class": INPUT_CLASS, "required": False}),
        }


class SchoolExamCreateForm(forms.Form):
    """Admin form to create exam(s): class + multi-section + optional teacher."""
    name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Mid Term, Final Exam"}))
    class_name = forms.ChoiceField(choices=[], widget=forms.Select(attrs={"class": BS_INPUT, "id": "id_exam_class"}))
    sections = forms.MultipleChoiceField(choices=[], required=True, widget=forms.CheckboxSelectMultiple())
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}))
    teacher = forms.TypedChoiceField(choices=[], required=False, empty_value=None, widget=forms.Select(attrs={"class": BS_INPUT}))

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        classrooms = ClassRoom.objects.select_related("academic_year").order_by("academic_year__start_date", "name")
        self.fields["class_name"].choices = [("", "Select Class")] + [(c.name, c.name) for c in classrooms]
        self.fields["sections"].choices = [(s.name, s.name) for s in Section.objects.order_by("name")]
        teachers = Teacher.objects.filter(user__school=school).select_related("user").order_by("user__first_name", "user__last_name")
        self.fields["teacher"].choices = [("", "No specific teacher")] + [(t.id, t.user.get_full_name() or t.user.username) for t in teachers]

    def clean_teacher(self):
        val = self.cleaned_data.get("teacher")
        if val is None or val == "":
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

class AttendanceForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ["student", "date", "status"]
        widgets = {
            "student": forms.Select(attrs={"class": INPUT_CLASS}),
            "date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "status": forms.Select(attrs={"class": INPUT_CLASS}),
        }


# ---- School Admin: Student Add Form ----
class StudentAddForm(forms.Form):
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "First Name"}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Last Name"}))
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Username"}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "placeholder": "Password"}))
    classroom = forms.ModelChoiceField(queryset=ClassRoom.objects.none(), widget=forms.Select(attrs={"class": BS_INPUT, "id": "id_classroom"}))
    section = forms.ModelChoiceField(queryset=Section.objects.none(), widget=forms.Select(attrs={"class": BS_INPUT, "id": "id_section"}))
    roll_number = forms.CharField(max_length=50, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Roll Number"}))
    admission_number = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Admission Number"}))
    date_of_birth = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}))
    parent_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Parent Name"}))
    parent_phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Parent Phone"}))

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.fields["classroom"].queryset = ClassRoom.objects.select_related("academic_year").order_by("academic_year", "name")
        self.fields["section"].queryset = Section.objects.order_by("name")

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Username already exists.")
        return username

    def clean_admission_number(self):
        adm = self.cleaned_data.get("admission_number")
        if adm and Student.objects.filter(admission_number=adm).exists():
            raise forms.ValidationError("Admission number already exists for this school.")
        return adm

    def clean(self):
        data = super().clean()
        section = data.get("section")
        classroom = data.get("classroom")
        roll = data.get("roll_number")
        if section and classroom and section not in classroom.sections.all():
            raise forms.ValidationError("Section must belong to selected class.")
        if section and classroom and roll and Student.objects.filter(classroom=classroom, section=section, roll_number=roll).exists():
            raise forms.ValidationError("Roll number already exists for this class-section.")
        return data


# ---- School Admin: Student Edit Form ----
class StudentEditForm(forms.Form):
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    classroom = forms.ModelChoiceField(queryset=ClassRoom.objects.none(), required=False, widget=forms.Select(attrs={"class": BS_INPUT}))
    section = forms.ModelChoiceField(queryset=Section.objects.none(), required=False, widget=forms.Select(attrs={"class": BS_INPUT}))
    roll_number = forms.CharField(max_length=50, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    admission_number = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    date_of_birth = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}))
    parent_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    parent_phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))

    def __init__(self, school, student=None, data=None, initial=None, **kwargs):
        super().__init__(data=data, initial=initial, **kwargs)
        self.school = school
        self.student = student
        self.fields["classroom"].queryset = ClassRoom.objects.select_related("academic_year").order_by("academic_year", "name")
        self.fields["section"].queryset = Section.objects.order_by("name")

    def clean_admission_number(self):
        adm = self.cleaned_data.get("admission_number")
        if not adm:
            return adm
        qs = Student.objects.filter(admission_number=adm)
        if self.student:
            qs = qs.exclude(pk=self.student.pk)
        if qs.exists():
            raise forms.ValidationError("Admission number already exists.")
        return adm

    def clean(self):
        data = super().clean()
        section = data.get("section")
        classroom = data.get("classroom")
        roll = data.get("roll_number")
        if section and classroom and section not in classroom.sections.all():
            raise forms.ValidationError("Section must belong to selected class.")
        if section and classroom and roll and self.student:
            if Student.objects.filter(classroom=classroom, section=section, roll_number=roll).exclude(pk=self.student.pk).exists():
                raise forms.ValidationError("Roll number already exists for this class-section.")
        return data


# ---- School Admin: Teacher Add Form ----
class TeacherAddForm(forms.Form):
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "First Name"}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Last Name"}))
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Username"}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "placeholder": "Password"}))
    subjects = forms.ModelMultipleChoiceField(
        queryset=Subject.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": BS_INPUT, "id": "id_subjects"}),
    )
    classrooms = forms.ModelMultipleChoiceField(
        queryset=ClassRoom.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": BS_INPUT, "id": "id_classrooms"}),
    )
    employee_id = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Employee ID"}))
    phone_number = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Phone"}))

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subjects"].queryset = Subject.objects.order_by("name")
        self.fields["classrooms"].queryset = ClassRoom.objects.order_by("name", "section")

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Username already exists.")
        return username


# ---- School Admin: Teacher Edit Form ----
class TeacherEditForm(forms.Form):
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={"class": INPUT_CLASS}))
    phone_number = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    qualification = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    experience = forms.CharField(max_length=100, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. 5 years"}))
    role = forms.ChoiceField(choices=User.Roles.choices, widget=forms.Select(attrs={"class": BS_INPUT}))
    subjects = forms.ModelMultipleChoiceField(
        queryset=Subject.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": BS_INPUT, "id": "id_edit_subjects"}),
    )
    classrooms = forms.ModelMultipleChoiceField(
        queryset=ClassRoom.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": BS_INPUT}),
    )

    def __init__(self, school, teacher=None, data=None, initial=None, **kwargs):
        super().__init__(data=data, initial=initial, **kwargs)
        self.school = school
        self.teacher = teacher
        self.fields["subjects"].queryset = Subject.objects.order_by("name")
        self.fields["classrooms"].queryset = ClassRoom.objects.select_related("academic_year").order_by("academic_year", "name")
        if teacher:
            self.fields["role"].initial = teacher.user.role

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if not email:
            return email
        qs = User.objects.filter(email__iexact=email, school=self.school)
        if self.teacher:
            qs = qs.exclude(pk=self.teacher.user_id)
        if qs.exists():
            raise forms.ValidationError("This email is already used by another user in this school.")
        return email

    def clean(self):
        data = super().clean()
        return data


# ---- Student Bulk Import (CSV) ----
class StudentBulkImportForm(forms.Form):
    csv_file = forms.FileField(
        label="CSV File",
        help_text="Columns: name, username, password, class, section, roll_number",
        widget=forms.FileInput(attrs={"class": INPUT_CLASS, "accept": ".csv"}),
    )


# ---- School Admin: Academic Year ----
class AcademicYearForm(forms.ModelForm):
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}), input_formats=["%Y-%m-%d"])
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}), input_formats=["%Y-%m-%d"])

    class Meta:
        model = AcademicYear
        fields = ["name", "start_date", "end_date"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. 2025-2026"}),
        }

    def clean(self):
        from datetime import date
        data = super().clean()
        start = data.get("start_date")
        end = data.get("end_date")
        if start and end and end <= start:
            raise forms.ValidationError("End date must be after start date.")
        if self.instance and self.instance.pk and end and end < date.today():
            raise forms.ValidationError("Cannot edit an academic year that has already ended.")
        return data


# ---- School Admin: Class (Grade) ----
class ClassRoomForm(forms.ModelForm):
    class Meta:
        model = ClassRoom
        fields = ["name", "description", "capacity", "academic_year", "sections"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Grade 1, Grade 10"}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Optional description"}),
            "capacity": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "placeholder": "Optional"}),
            "academic_year": forms.Select(attrs={"class": BS_INPUT}),
            # CheckboxSelectMultiple renders one checkbox per section (we render them as "cards" in the template).
            "sections": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.fields["academic_year"].queryset = AcademicYear.objects.order_by("-start_date")
        self.fields["sections"].queryset = Section.objects.order_by("name")
        self.fields["sections"].required = False

    def clean(self):
        data = super().clean()
        return data


# ---- School Admin: Section CRUD ----
class SectionForm(forms.ModelForm):
    """Section is independent (A, B, C, etc.)."""
    class Meta:
        model = Section
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. A, B, C"}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Optional description"}),
        }

    def __init__(self, school=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school


# ---- School Admin: Subject ----
class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ["name", "code", "classroom", "teacher", "academic_year"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Mathematics, Physics"}),
            "code": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. MATH101, PHY101"}),
            "classroom": forms.Select(attrs={"class": BS_INPUT}),
            "teacher": forms.Select(attrs={"class": BS_INPUT}),
            "academic_year": forms.Select(attrs={"class": BS_INPUT}),
        }

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.fields["classroom"].queryset = ClassRoom.objects.select_related("academic_year").order_by("academic_year", "name")
        self.fields["teacher"].queryset = Teacher.objects.select_related("user").order_by("user__first_name")
        self.fields["academic_year"].queryset = AcademicYear.objects.order_by("-start_date")
        self.fields["teacher"].required = False
        self.fields["code"].required = False

    def clean(self):
        data = super().clean()
        return data


# ---- Admin Frontend: School / Teacher / Student (SuperAdmin) ----
class AdminSchoolForm(forms.ModelForm):
    """School form for /admin/schools/ - SuperAdmin creates/edits schools."""
    class Meta:
        model = School
        fields = ["name", "saas_plan", "plan", "address", "contact_email", "phone"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "School Name"}),
            "saas_plan": forms.Select(attrs={"class": BS_INPUT}),
            "plan": forms.Select(attrs={"class": BS_INPUT}),
            "address": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Address"}),
            "contact_email": forms.EmailInput(attrs={"class": INPUT_CLASS, "placeholder": "contact@school.edu"}),
            "phone": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "+1234567890"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["saas_plan"].queryset = Plan.objects.all().order_by("price_per_student")
        self.fields["saas_plan"].required = False
        self.fields["saas_plan"].label = "SaaS Plan (Starter/Growth/Enterprise)"
        self.fields["plan"].queryset = SubscriptionPlan.objects.filter(is_active=True).order_by("price_per_student")


class AdminTeacherForm(forms.Form):
    """Teacher form - Username, Email, Password, Name, Phone, Qualification, Assigned School."""
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Username"}))
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "First Name"}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Last Name"}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"class": INPUT_CLASS, "placeholder": "email@example.com"}))
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "placeholder": "Password"}),
    )
    confirm_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "placeholder": "Confirm Password"}),
    )
    phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Phone"}))
    qualification = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. B.Ed, M.Sc"}))
    school = forms.ModelChoiceField(queryset=School.objects.all().order_by("name"), widget=forms.Select(attrs={"class": BS_INPUT}))

    def __init__(self, *args, for_create=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.for_create = for_create
        if for_create:
            self.fields["password"].required = True
            self.fields["confirm_password"].required = True

    def clean(self):
        data = super().clean()
        if self.for_create:
            password = data.get("password")
            confirm = data.get("confirm_password")
            if password and confirm and password != confirm:
                raise forms.ValidationError("Passwords do not match.")
            if password and len(password) < 8:
                raise forms.ValidationError("Password must be at least 8 characters.")
        return data


class AdminStudentForm(forms.Form):
    """Student form - Name, Admission Number, Password, Class, Section, School."""
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "First Name"}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Last Name"}))
    admission_number = forms.CharField(max_length=50, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Admission Number"}))
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "placeholder": "Password"}),
    )
    confirm_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "placeholder": "Confirm Password"}),
    )
    school = forms.ModelChoiceField(queryset=School.objects.all().order_by("name"), widget=forms.Select(attrs={"class": BS_INPUT, "id": "id_school"}))
    classroom = forms.ModelChoiceField(queryset=ClassRoom.objects.none(), required=False, widget=forms.Select(attrs={"class": BS_INPUT, "id": "id_classroom"}))
    section = forms.ModelChoiceField(queryset=Section.objects.none(), required=False, widget=forms.Select(attrs={"class": BS_INPUT, "id": "id_section"}))
    roll_number = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Roll Number"}))

    def __init__(self, *args, for_create=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.for_create = for_create
        if for_create:
            self.fields["password"].required = True
            self.fields["confirm_password"].required = True
        # Classroom and section are loaded per-school via AJAX (tenant schemas)
        self.fields["classroom"].queryset = ClassRoom.objects.none()
        self.fields["section"].queryset = Section.objects.none()

    def clean(self):
        data = super().clean()
        if self.for_create:
            password = data.get("password")
            confirm = data.get("confirm_password")
            if password and confirm and password != confirm:
                raise forms.ValidationError("Passwords do not match.")
            if password and len(password) < 8:
                raise forms.ValidationError("Password must be at least 8 characters.")
        classroom = data.get("classroom")
        section = data.get("section")
        if section and classroom and section.classroom != classroom:
            raise forms.ValidationError("Section must belong to selected class.")
        return data


# ---------- Fee & Billing ----------

class FeeTypeForm(forms.ModelForm):
    class Meta:
        model = FeeType
        fields = ["name", "code"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Tuition, Transport"}),
            "code": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Optional code"}),
        }


class FeeStructureForm(forms.ModelForm):
    class Meta:
        model = FeeStructure
        fields = ["fee_type", "classroom", "amount", "academic_year"]
        widgets = {
            "fee_type": forms.Select(attrs={"class": BS_INPUT}),
            "classroom": forms.Select(attrs={"class": BS_INPUT}),
            "amount": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "step": "0.01"}),
            "academic_year": forms.Select(attrs={"class": BS_INPUT}),
        }

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fee_type"].queryset = FeeType.objects.all()
        self.fields["classroom"].queryset = ClassRoom.objects.all()
        self.fields["academic_year"].queryset = AcademicYear.objects.order_by("-start_date")

class PaymentForm(forms.ModelForm):
    payment_method = forms.ChoiceField(
        choices=[
            ("Cash", "Cash"),
            ("Card", "Card"),
            ("Bank Transfer", "Bank Transfer"),
            ("UPI", "UPI"),
            ("Cheque", "Cheque"),
        ],
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )

    class Meta:
        model = Payment
        fields = ["amount", "payment_date", "payment_method", "receipt_number", "notes"]
        widgets = {
            "amount": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "step": "0.01"}),
            "payment_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "receipt_number": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "notes": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2}),
        }


class StaffAttendanceForm(forms.ModelForm):
    class Meta:
        model = StaffAttendance
        fields = ["teacher", "date", "status", "remarks"]
        widgets = {
            "teacher": forms.Select(attrs={"class": BS_INPUT}),
            "date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "status": forms.Select(attrs={"class": BS_INPUT}),
            "remarks": forms.TextInput(attrs={"class": INPUT_CLASS}),
        }


class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = ["name", "sku", "unit", "quantity", "min_stock", "unit_price"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "sku": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "unit": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "quantity": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
            "min_stock": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
            "unit_price": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "step": "0.01"}),
        }


class PurchaseForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = ["inventory_item", "quantity", "unit_price", "purchase_date", "supplier", "reference"]
        widgets = {
            "inventory_item": forms.Select(attrs={"class": BS_INPUT}),
            "quantity": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
            "unit_price": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "step": "0.01"}),
            "purchase_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "supplier": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "reference": forms.TextInput(attrs={"class": INPUT_CLASS}),
        }


class SupportTicketForm(forms.ModelForm):
    priority = forms.ChoiceField(
        choices=[
            ("LOW", "Low"),
            ("MEDIUM", "Medium"),
            ("HIGH", "High"),
            ("PRIORITY", "Priority (Pro Plan)"),
        ],
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )

    class Meta:
        model = SupportTicket
        fields = ["subject", "message", "priority"]
        widgets = {
            "subject": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Brief subject"}),
            "message": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 4, "placeholder": "Describe your issue..."}),
            "priority": forms.Select(attrs={"class": BS_INPUT}),
        }


# ---------- Pro Plan Forms ----------

class OnlineAdmissionForm(forms.Form):
    """Public online admission form."""
    first_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    last_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"class": INPUT_CLASS}))
    phone = forms.CharField(max_length=20, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}))
    parent_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    parent_phone = forms.CharField(max_length=20, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 2, "class": INPUT_CLASS}), required=False)
    applied_class = forms.ModelChoiceField(
        queryset=ClassRoom.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["applied_class"].queryset = ClassRoom.objects.all().order_by("name")


class BookForm(forms.ModelForm):
    class Meta:
        model = Book
        fields = ["title", "author", "isbn", "category", "total_copies"]
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "author": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "isbn": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "category": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "total_copies": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1}),
        }


class BookIssueForm(forms.Form):
    book = forms.ModelChoiceField(queryset=Book.objects.none(), widget=forms.Select(attrs={"class": BS_INPUT}))
    student = forms.ModelChoiceField(queryset=Student.objects.none(), widget=forms.Select(attrs={"class": BS_INPUT}))
    issue_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}))
    due_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}))

    def __init__(self, school=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["book"].queryset = Book.objects.all()
        self.fields["student"].queryset = Student.objects.all()


class HostelForm(forms.ModelForm):
    class Meta:
        model = Hostel
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"class": INPUT_CLASS})}


class HostelRoomForm(forms.ModelForm):
    class Meta:
        model = HostelRoom
        fields = ["room_number", "capacity", "room_type"]
        widgets = {
            "room_number": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "capacity": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1}),
            "room_type": forms.TextInput(attrs={"class": INPUT_CLASS}),
        }


class RouteForm(forms.ModelForm):
    class Meta:
        model = Route
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2}),
        }


class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = ["registration_number", "vehicle_type", "capacity", "route"]
        widgets = {
            "registration_number": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "vehicle_type": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "capacity": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1}),
            "route": forms.Select(attrs={"class": BS_INPUT}),
        }
