INPUT_CLASS = "form-control"
BS_INPUT = "form-control form-select"  # for selects

from django import forms
from django.contrib.auth import get_user_model
from .models import (
    Homework,
    Marks,
    Attendance,
    Exam,
    Student,
    Teacher,
    Section,
    ClassRoom,
    Subject,
    School,
    AcademicYear,
)

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


class TeacherHomeworkForm(forms.ModelForm):
    """Homework form for teachers - subject is set from teacher's assignment."""
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
        fields = ["name", "classroom", "start_date", "end_date"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Mid Term, Final Exam"}),
            "classroom": forms.Select(attrs={"class": INPUT_CLASS}),
            "start_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
        }


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
        self.fields["classroom"].queryset = ClassRoom.objects.filter(school=school).order_by("name", "section")
        self.fields["section"].queryset = Section.objects.filter(school=school).order_by("classroom", "name")

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Username already exists.")
        return username

    def clean_admission_number(self):
        adm = self.cleaned_data.get("admission_number")
        if adm and Student.objects.filter(user__school=self.school, admission_number=adm).exists():
            raise forms.ValidationError("Admission number already exists for this school.")
        return adm

    def clean(self):
        data = super().clean()
        section = data.get("section")
        classroom = data.get("classroom")
        roll = data.get("roll_number")
        if section and classroom and section.classroom_id != classroom.id:
            raise forms.ValidationError("Section must belong to selected classroom.")
        if section and roll and Student.objects.filter(section=section, roll_number=roll).exists():
            raise forms.ValidationError("Roll number already exists in this section.")
        if section and section.capacity is not None:
            current = Student.objects.filter(section=section).count()
            if current >= section.capacity:
                raise forms.ValidationError("Section has reached its capacity (%s)." % section.capacity)
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
        self.fields["classroom"].queryset = ClassRoom.objects.filter(school=school).order_by("name", "section")
        self.fields["section"].queryset = Section.objects.filter(school=school).order_by("classroom", "name")

    def clean_admission_number(self):
        adm = self.cleaned_data.get("admission_number")
        if not adm:
            return adm
        qs = Student.objects.filter(user__school=self.school, admission_number=adm)
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
        if section and classroom and section.classroom_id != classroom.id:
            raise forms.ValidationError("Section must belong to selected classroom.")
        if section and roll and self.student:
            if Student.objects.filter(section=section, roll_number=roll).exclude(pk=self.student.pk).exists():
                raise forms.ValidationError("Roll number already exists in this section.")
        if section and section.capacity is not None and self.student:
            others = Student.objects.filter(section=section).exclude(pk=self.student.pk).count()
            if others >= section.capacity:
                raise forms.ValidationError("Section has reached its capacity.")
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
        self.fields["subjects"].queryset = Subject.objects.filter(school=school).order_by("name")
        self.fields["classrooms"].queryset = ClassRoom.objects.filter(school=school).order_by("name", "section")

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
    sections = forms.ModelMultipleChoiceField(
        queryset=Section.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": BS_INPUT, "id": "id_edit_sections"}),
        help_text="Sections where this teacher is class teacher.",
    )

    def __init__(self, school, teacher=None, data=None, initial=None, **kwargs):
        super().__init__(data=data, initial=initial, **kwargs)
        self.school = school
        self.teacher = teacher
        self.fields["subjects"].queryset = Subject.objects.filter(school=school).order_by("name")
        self.fields["sections"].queryset = Section.objects.filter(school=school).select_related("classroom").order_by("classroom__name", "name")
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
        subjects = data.get("subjects") or []
        for s in subjects:
            if s.school != self.school:
                raise forms.ValidationError("Cannot assign subject from another school.")
        sections = data.get("sections") or []
        for sec in sections:
            if sec.school != self.school:
                raise forms.ValidationError("Cannot assign section from another school.")
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
        fields = ["name", "description", "capacity", "academic_year"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Grade 1, Grade 10"}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Optional description"}),
            "capacity": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "placeholder": "Optional"}),
            "academic_year": forms.Select(attrs={"class": BS_INPUT}),
        }

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.fields["academic_year"].queryset = AcademicYear.objects.filter(school=school).order_by("-start_date")

    def clean(self):
        data = super().clean()
        ay = data.get("academic_year")
        if ay and ay.school != self.school:
            raise forms.ValidationError("Invalid academic year.")
        return data


# ---- School Admin: Section CRUD ----
class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ["classroom", "name", "capacity", "class_teacher"]
        widgets = {
            "classroom": forms.Select(attrs={"class": BS_INPUT}),
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Alpha, Beta"}),
            "capacity": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "placeholder": "Optional"}),
            "class_teacher": forms.Select(attrs={"class": BS_INPUT}),
        }

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.fields["classroom"].queryset = ClassRoom.objects.filter(school=school).select_related("academic_year").order_by("academic_year", "name")
        self.fields["class_teacher"].queryset = Teacher.objects.filter(user__school=school).select_related("user").order_by("user__first_name")
        self.fields["class_teacher"].required = False

    def clean(self):
        data = super().clean()
        classroom = data.get("classroom")
        capacity = data.get("capacity")
        if classroom and classroom.school != self.school:
            raise forms.ValidationError("Invalid classroom.")
        if classroom and capacity is not None and classroom.capacity is not None and capacity > classroom.capacity:
            raise forms.ValidationError("Section capacity cannot exceed classroom capacity (%s)." % classroom.capacity)
        teacher = data.get("class_teacher")
        if teacher and teacher.user.school != self.school:
            raise forms.ValidationError("Teacher must belong to this school.")
        # Optional: prevent same teacher as class teacher in multiple sections
        if teacher:
            qs = Section.objects.filter(school=self.school, class_teacher=teacher)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("This teacher is already assigned as class teacher to another section.")
        return data


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
        self.fields["classroom"].queryset = ClassRoom.objects.filter(school=school).select_related("academic_year").order_by("academic_year", "name")
        self.fields["teacher"].queryset = Teacher.objects.filter(user__school=school).select_related("user").order_by("user__first_name")
        self.fields["academic_year"].queryset = AcademicYear.objects.filter(school=school).order_by("-start_date")
        self.fields["teacher"].required = False
        self.fields["code"].required = False

    def clean(self):
        data = super().clean()
        classroom = data.get("classroom")
        ay = data.get("academic_year")
        teacher = data.get("teacher")
        if classroom and classroom.school != self.school:
            raise forms.ValidationError("Invalid class.")
        if ay and ay.school != self.school:
            raise forms.ValidationError("Invalid academic year.")
        if teacher and teacher.user.school != self.school:
            raise forms.ValidationError("Teacher must belong to this school.")
        return data
