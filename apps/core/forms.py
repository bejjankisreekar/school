INPUT_CLASS = "form-control"
BS_INPUT = "form-control form-select"  # for selects

from decimal import Decimal
import re

from django import forms
from django.forms.models import BaseInlineFormSet, inlineformset_factory
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import (
    CommonPasswordValidator,
    get_default_password_validators,
    validate_password,
)
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Q, Sum
from django.utils import timezone
from apps.customers.models import Coupon, Plan, SaaSPlatformPayment, School, SchoolSubscription
from apps.school_data.classroom_ordering import ORDER_AY_PK_GRADE_NAME, ORDER_AY_START_GRADE_NAME, ORDER_GRADE_NAME
from apps.school_data.models import (
    Homework,
    Marks,
    Attendance,
    Exam,
    ExamSession,
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
    StudentPromotion,
    HolidayEvent,
    WorkingSundayOverride,
    MasterDataOption,
)
from .models import ContactEnquiry, SchoolEnrollmentRequest

User = get_user_model()

class StudentTeacherMessageForm(forms.Form):
    teacher = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=True,
        label="Teacher",
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )
    content = forms.CharField(
        required=True,
        label="Message",
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Type your message..."}),
    )

    def __init__(self, *args, teacher_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if teacher_qs is not None:
            self.fields["teacher"].queryset = teacher_qs


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
        fields = [
            "title",
            "subject",
            "teacher",
            "homework_type",
            "priority",
            "status",
            "assigned_date",
            "due_date",
            "estimated_duration_minutes",
            "max_marks",
            "submission_type",
            "submission_required",
            "allow_late_submission",
            "late_submission_until",
            "description",
            "instructions",
            "academic_year",
            "assigned_by",
            "classes",
            "sections",
            "attachment",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Homework title"}),
            "subject": forms.Select(attrs={"class": BS_INPUT}),
            "teacher": forms.Select(attrs={"class": BS_INPUT}),
            "homework_type": forms.Select(attrs={"class": BS_INPUT}),
            "priority": forms.Select(attrs={"class": BS_INPUT}),
            "status": forms.Select(attrs={"class": BS_INPUT}),
            "assigned_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "due_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "estimated_duration_minutes": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": 0, "placeholder": "e.g. 30, 60"}
            ),
            "max_marks": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "placeholder": "Optional"}),
            "submission_type": forms.Select(attrs={"class": BS_INPUT}),
            "submission_required": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "allow_late_submission": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "late_submission_until": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": INPUT_CLASS},
                format="%Y-%m-%dT%H:%M",
            ),
            "description": forms.Textarea(
                attrs={"class": INPUT_CLASS, "rows": 4, "placeholder": "What students should do"}
            ),
            "instructions": forms.Textarea(
                attrs={
                    "class": INPUT_CLASS,
                    "rows": 3,
                    "placeholder": "Format, materials, how to submit (optional)",
                }
            ),
            "academic_year": forms.Select(attrs={"class": BS_INPUT}),
            "assigned_by": forms.Select(attrs={"class": BS_INPUT}),
            "classes": forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
            "sections": forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
            "attachment": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }
        labels = {
            "subject": "Subject",
            "teacher": "Assigned teacher",
            "homework_type": "Work type",
            "max_marks": "Max marks",
            "estimated_duration_minutes": "Est. time (minutes)",
            "late_submission_until": "Late submission until",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.school_data.models import ClassSectionSubjectTeacher
        from datetime import date as date_cls

        self.fields["late_submission_until"].required = False
        self.fields["late_submission_until"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ]
        if self.instance.pk and self.instance.late_submission_until:
            dt = self.instance.late_submission_until
            if hasattr(dt, "strftime"):
                self.initial.setdefault(
                    "late_submission_until",
                    timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M"),
                )

        self.fields["classes"].required = True
        self.fields["sections"].required = True
        self.fields["subject"].required = True
        self.fields["subject"].empty_label = "Select subject"
        self.fields["academic_year"].required = False
        self.fields["academic_year"].empty_label = "Infer from class (or pick year)"
        self.fields["max_marks"].required = False
        self.fields["estimated_duration_minutes"].required = False
        self.fields["instructions"].required = False

        if not self.instance.pk:
            self.initial.setdefault("assigned_date", date_cls.today())

        if user and getattr(user, "role", None) == "ADMIN":
            self.fields["subject"].queryset = Subject.objects.order_by("display_order", "name")
            self.fields["teacher"].required = False
            self.fields["teacher"].empty_label = "Any (not assigned to a specific teacher)"
            self.fields["teacher"].queryset = Teacher.objects.select_related("user").order_by(
                "user__first_name", "user__last_name", "user__username"
            )
            self.fields["classes"].queryset = ClassRoom.objects.select_related("academic_year").order_by(
                *ORDER_AY_START_GRADE_NAME
            )
            self.fields["sections"].queryset = Section.objects.order_by("name")
            self.fields["assigned_by"].required = False
            self.fields["assigned_by"].empty_label = "Me (current user)"
            self.fields["assigned_by"].queryset = User.objects.filter(
                school=user.school,
                role__in=[User.Roles.ADMIN, User.Roles.TEACHER],
            ).order_by("first_name", "last_name", "username")
            self.fields["academic_year"].queryset = AcademicYear.objects.order_by("-start_date")
        elif user and getattr(user, "role", None) == "TEACHER":
            teacher = getattr(user, "teacher_profile", None)
            if teacher:
                from apps.core.utils import teacher_class_section_pairs_display

                subj_ids = set(
                    ClassSectionSubjectTeacher.objects.filter(teacher=teacher).values_list("subject_id", flat=True)
                )
                subj_ids |= set(teacher.subjects.values_list("id", flat=True))
                if teacher.subject_id:
                    subj_ids.add(teacher.subject_id)
                self.fields["subject"].queryset = (
                    Subject.objects.filter(id__in=subj_ids).order_by("display_order", "name")
                    if subj_ids
                    else Subject.objects.none()
                )

                pairs = teacher_class_section_pairs_display(teacher)
                class_ids = set()
                section_ids = set()
                for cn, sn in pairs:
                    cr = ClassRoom.objects.filter(name__iexact=cn).first()
                    if cr:
                        class_ids.add(cr.id)
                    sec = Section.objects.filter(name__iexact=sn).first()
                    if sec:
                        section_ids.add(sec.id)
                self.fields["classes"].queryset = (
                    ClassRoom.objects.filter(id__in=class_ids)
                    .select_related("academic_year")
                    .order_by(*ORDER_AY_START_GRADE_NAME)
                    if class_ids
                    else ClassRoom.objects.none()
                )
                self.fields["sections"].queryset = (
                    Section.objects.filter(id__in=section_ids).order_by("name") if section_ids else Section.objects.none()
                )
                self.fields["academic_year"].queryset = AcademicYear.objects.order_by("-start_date")
            else:
                self.fields["subject"].queryset = Subject.objects.none()
                self.fields["classes"].queryset = ClassRoom.objects.none()
                self.fields["sections"].queryset = Section.objects.none()
                self.fields["academic_year"].queryset = AcademicYear.objects.order_by("-start_date")
        else:
            self.fields["subject"].queryset = Subject.objects.order_by("display_order", "name")
            self.fields["classes"].queryset = ClassRoom.objects.order_by(*ORDER_GRADE_NAME)
            self.fields["sections"].queryset = Section.objects.order_by("name")
            self.fields["academic_year"].queryset = AcademicYear.objects.order_by("-start_date")

        if not user or getattr(user, "role", None) != "ADMIN":
            self.fields.pop("assigned_by", None)
            self.fields.pop("teacher", None)

    def save(self, commit=True):
        obj = super().save(commit=False)
        classes = self.cleaned_data.get("classes")
        if not obj.academic_year_id and classes:
            cr_list = list(classes)
            if cr_list and getattr(cr_list[0], "academic_year_id", None):
                obj.academic_year_id = cr_list[0].academic_year_id
        if commit:
            obj.save()
            self.save_m2m()
        return obj


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
    """Deprecated for new flows; use TeacherExamSessionPaperForm. Kept for migrations/tests."""

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
            self.fields["class_name"].choices = [(c.name, c.name) for c in ClassRoom.objects.order_by(*ORDER_GRADE_NAME)]
            self.fields["section"].choices = [(s.name, s.name) for s in Section.objects.order_by("name")]


class SchoolExamSessionEditForm(forms.ModelForm):
    """School admin: edit exam session metadata (not individual papers).

    Omits ``display_order`` so edit works on tenant DBs that predate migration 0039/0041
    (column may not exist). Order stays at model default until migrations are applied.
    """

    class Meta:
        model = ExamSession
        fields = ["name", "classroom", "class_name", "section"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "e.g. Annual Exam 2026", "maxlength": 100}
            ),
            "classroom": forms.Select(attrs={"class": BS_INPUT}),
            "class_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Class name"}),
            "section": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Section"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["classroom"].queryset = ClassRoom.objects.select_related("academic_year").order_by(
            *ORDER_AY_START_GRADE_NAME
        )
        self.fields["classroom"].required = False
        self.fields["class_name"].required = False
        self.fields["section"].required = False


class ExamSessionPaperInlineForm(forms.ModelForm):
    """One subject paper row when editing an exam session (admin)."""

    mark_components_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"class": "exam-paper-mark-components-json"}),
    )

    class Meta:
        model = Exam
        fields = [
            "name",
            "subject",
            "date",
            "start_time",
            "end_time",
            "teacher",
            "total_marks",
            "marks_teacher_edit_locked",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Paper title"}),
            "date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "start_time": forms.TimeInput(attrs={"type": "time", "class": INPUT_CLASS}),
            "end_time": forms.TimeInput(attrs={"type": "time", "class": INPUT_CLASS}),
            "total_marks": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "max": 1000}),
            "marks_teacher_edit_locked": forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            ),
        }

    def __init__(self, *args, school=None, **kwargs):
        self.school = school
        super().__init__(*args, **kwargs)
        self.fields["subject"].queryset = Subject.objects.order_by("display_order", "name")
        self.fields["subject"].widget.attrs.update({"class": BS_INPUT})
        self.fields["subject"].required = False
        self.fields["name"].required = False
        self.fields["date"].required = False
        if school:
            self.fields["teacher"].queryset = Teacher.objects.filter(user__school=school).select_related(
                "user"
            ).order_by("user__first_name", "user__last_name", "user__username")
        else:
            self.fields["teacher"].queryset = Teacher.objects.none()
        self.fields["teacher"].required = False
        self.fields["teacher"].widget.attrs.update({"class": BS_INPUT})
        self.fields["total_marks"].required = False
        self.fields["marks_teacher_edit_locked"].required = False
        import json as _json

        if self.instance and self.instance.pk:
            comps = list(
                self.instance.mark_components.order_by("sort_order", "id").values(
                    "component_name", "max_marks"
                )
            )
            payload = [{"name": c["component_name"], "marks": c["max_marks"]} for c in comps]
            self.fields["mark_components_json"].initial = _json.dumps(payload)
        else:
            self.fields["mark_components_json"].initial = "[]"

    def clean(self):
        data = super().clean()
        if data.get("DELETE"):
            return data
        subj = data.get("subject")
        dt = data.get("date")
        name = (data.get("name") or "").strip()
        # Skip validation for completely empty extra rows
        if not subj and not dt and not name and not self.instance.pk:
            return data
        if not subj:
            raise forms.ValidationError("Subject is required for each paper row you fill in.")
        if not dt:
            raise forms.ValidationError("Exam date is required.")
        st = data.get("start_time")
        et = data.get("end_time")
        if st and et and et <= st:
            raise forms.ValidationError("End time must be after start time.")
        if (st and not et) or (et and not st):
            raise forms.ValidationError("Set both start and end time, or leave both empty for an all-day paper.")

        session = getattr(self.instance, "session", None)
        if not session or not session.pk:
            return data

        meta = getattr(self.formset, "session_meta", None) or {}
        cn = (meta.get("class_name") or "").strip() or (session.class_name or "").strip()
        sn = (meta.get("section") or "").strip() or (session.section or "").strip()
        teacher_obj = data.get("teacher")
        tid = teacher_obj.pk if teacher_obj else None
        pk = self.instance.pk if self.instance.pk else None

        from apps.core import views as exam_views

        if exam_views._exam_class_section_date_conflict_outside_session(cn, sn, dt, session.pk, exclude_pk=pk):
            raise forms.ValidationError(
                "Another exam outside this session already uses this class, section, and date."
            )
        if exam_views._exam_teacher_date_conflict(tid, dt, exclude_pk=pk):
            raise forms.ValidationError("This teacher already has another exam on that date.")
        if exam_views._exam_duplicate(cn, sn, dt, subj, exclude_pk=pk):
            raise forms.ValidationError("An exam already exists for this class, section, date, and subject.")
        raw_mc = (data.get("mark_components_json") or "").strip()
        if raw_mc:
            from django.core.exceptions import ValidationError as DjangoValidationError

            from apps.core.exam_components import parse_components_from_json, normalize_component_items

            try:
                parsed = parse_components_from_json(raw_mc)
                if parsed is None:
                    parsed = []
                normalize_component_items(parsed)
            except DjangoValidationError as e:
                msg = e.messages[0] if getattr(e, "messages", None) else str(e)
                raise forms.ValidationError(msg) from e
            except Exception as exc:
                raise forms.ValidationError(f"Invalid mark components: {exc}") from exc
        return data


class ExamPaperInlineFormSet(BaseInlineFormSet):
    """Passes school into each paper form; ``session_meta`` is set from the session form for validation."""

    def __init__(self, *args, school=None, **kwargs):
        self.school = school
        self.session_meta = {}
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["school"] = self.school
        return kwargs


ExamSessionPaperFormSet = inlineformset_factory(
    ExamSession,
    Exam,
    form=ExamSessionPaperInlineForm,
    formset=ExamPaperInlineFormSet,
    fk_name="session",
    extra=2,
    can_delete=True,
    min_num=0,
    validate_min=False,
)


class TeacherExamSessionPaperForm(forms.Form):
    """Teacher: creates one exam session + one subject paper (real ERP structure)."""

    use_mark_components = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_teacher_use_mark_components"}),
    )
    mark_components_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_teacher_mark_components_json"}),
    )

    session_name = forms.CharField(
        max_length=100,
        label="Exam session name",
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASS, "placeholder": "e.g. Annual Exam 2026"}
        ),
    )
    subject = forms.ModelChoiceField(
        queryset=Subject.objects.none(),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    class_name = forms.ChoiceField(choices=[], widget=forms.Select(attrs={"class": INPUT_CLASS}))
    section = forms.ChoiceField(choices=[], widget=forms.Select(attrs={"class": INPUT_CLASS}))
    date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
    )
    total_marks = forms.IntegerField(
        min_value=1,
        max_value=1000,
        initial=100,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1}),
    )

    def __init__(self, *args, allowed_pairs=None, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed_pairs = list(allowed_pairs or [])
        self.teacher = teacher
        self.fields["subject"].queryset = Subject.objects.order_by("display_order", "name")
        class_names = sorted({c for c, _ in self.allowed_pairs if c})
        section_names = sorted({s for _, s in self.allowed_pairs if s})
        self.fields["class_name"].choices = [("", "Select class")] + [(n, n) for n in class_names]
        self.fields["section"].choices = [("", "Select section")] + [(n, n) for n in section_names]
        self.fields["mark_components_json"].initial = "[]"

    def clean(self):
        from apps.school_data.models import ClassSectionSubjectTeacher

        data = super().clean()
        cn = (data.get("class_name") or "").strip()
        sn = (data.get("section") or "").strip()
        subj = data.get("subject")
        if not cn or not sn:
            return data
        allowed = {(str(c).strip().lower(), str(s).strip().lower()) for c, s in self.allowed_pairs}
        if (cn.lower(), sn.lower()) not in allowed:
            raise forms.ValidationError(
                "You can only create exams for class–sections you are assigned to."
            )
        if self.teacher and subj:
            csst_ok = ClassSectionSubjectTeacher.objects.filter(
                teacher=self.teacher,
                class_obj__name__iexact=cn,
                section__name__iexact=sn,
                subject=subj,
            ).exists()
            if not csst_ok:
                subj_ids = set(self.teacher.subjects.values_list("id", flat=True))
                if self.teacher.subject_id:
                    subj_ids.add(self.teacher.subject_id)
                if subj.id not in subj_ids:
                    raise forms.ValidationError(
                        "You are not mapped to teach this subject for the selected class and section."
                    )
        if data.get("use_mark_components"):
            from django.core.exceptions import ValidationError as DjangoValidationError

            from apps.core.exam_components import parse_components_from_json, normalize_component_items

            raw = (data.get("mark_components_json") or "").strip() or "[]"
            try:
                parsed = parse_components_from_json(raw)
                if parsed is None:
                    parsed = []
                normalize_component_items(parsed)
            except DjangoValidationError as e:
                msg = e.messages[0] if getattr(e, "messages", None) else str(e)
                raise forms.ValidationError(msg) from e
        return data


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


ENROLL_COUNTRY_CODES = (
    ("+91", "India (+91)"),
    ("+971", "United Arab Emirates (+971)"),
    ("+966", "Saudi Arabia (+966)"),
    ("+965", "Kuwait (+965)"),
    ("+974", "Qatar (+974)"),
    ("+973", "Bahrain (+973)"),
    ("+968", "Oman (+968)"),
    ("+92", "Pakistan (+92)"),
    ("+880", "Bangladesh (+880)"),
    ("+94", "Sri Lanka (+94)"),
    ("+977", "Nepal (+977)"),
    ("+1", "United States / Canada (+1)"),
    ("+44", "United Kingdom (+44)"),
    ("+61", "Australia (+61)"),
    ("+65", "Singapore (+65)"),
    ("+60", "Malaysia (+60)"),
    ("+353", "Ireland (+353)"),
    ("+49", "Germany (+49)"),
    ("+33", "France (+33)"),
)


def _enroll_tri_bool_field(label: str) -> forms.TypedChoiceField:
    def _coerce(v):
        if v in (None, ""):
            return None
        if v == "yes":
            return True
        if v == "no":
            return False
        return None

    return forms.TypedChoiceField(
        label=label,
        required=False,
        choices=[("", "—"), ("yes", "Yes"), ("no", "No")],
        coerce=_coerce,
        empty_value=None,
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )


ENROLL_STREAM_CHOICES = [
    ("science", "Science"),
    ("commerce", "Commerce"),
    ("arts", "Arts"),
]


class SchoolEnrollmentSignupForm(forms.ModelForm):
    """Public /enroll/ — request a new school tenant (no login)."""

    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": INPUT_CLASS,
                "autocomplete": "new-password",
                "placeholder": "Choose a secure password",
            }
        ),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": INPUT_CLASS,
                "autocomplete": "new-password",
                "placeholder": "Confirm password",
            }
        ),
    )
    intended_plan = forms.CharField(
        required=False,
        initial="premium",
        widget=forms.HiddenInput(attrs={"id": "id_intended_plan"}),
    )
    country_code = forms.ChoiceField(
        label="Country code",
        choices=ENROLL_COUNTRY_CODES,
        initial="+91",
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )
    phone_local = forms.CharField(
        label="Mobile number",
        max_length=15,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "Digits only (e.g. 9876543210)",
                "autocomplete": "tel-national",
                "inputmode": "numeric",
                "pattern": r"[0-9]*",
                "maxlength": "15",
                "id": "id_phone_local",
            }
        ),
    )
    streams_offered = forms.MultipleChoiceField(
        label="Streams offered (optional)",
        required=False,
        choices=ENROLL_STREAM_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "d-flex flex-wrap gap-3"}),
    )
    notes = forms.CharField(
        required=False,
        max_length=250,
        label="Additional Notes / Requirements",
        widget=forms.Textarea(
            attrs={
                "class": INPUT_CLASS,
                "rows": 4,
                "placeholder": "Enter any custom requirements, branch details, onboarding notes, or special requests…",
                "maxlength": "250",
                "id": "id_enroll_notes",
            }
        ),
    )

    class Meta:
        model = SchoolEnrollmentRequest
        fields = [
            "institution_name",
            "society_name",
            "email",
            "contact_name",
            "address",
            "city",
            "state",
            "pincode",
            "student_count",
            "teacher_count",
            "branch_count",
            "preferred_username",
            "notes",
            "intended_plan",
            "website",
            "affiliation_board",
            "school_type",
            "established_year",
            "school_motto",
            "affiliation_number",
            "landmark",
            "district",
            "latitude",
            "longitude",
            "maps_url",
            "alternate_contact_name",
            "alternate_contact_phone",
            "admin_designation",
            "admin_profile_photo",
            "instruction_medium",
            "streams_offered",
            "sections_per_class_notes",
            "curriculum_type",
            "total_classrooms",
            "lab_physics",
            "lab_chemistry",
            "lab_computer",
            "has_library",
            "has_playground",
            "has_transport",
            "total_student_capacity",
            "current_student_strength",
            "non_teaching_staff_count",
            "uses_erp",
            "current_erp_name",
            "require_data_migration",
            "preferred_ui_language",
            "expected_start_date",
            "detailed_requirements",
            "school_logo",
            "registration_certificate",
            "address_proof",
            "other_documents",
        ]
        widgets = {
            "institution_name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Official school name",
                    "autocomplete": "organization",
                }
            ),
            "society_name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Housing society / trust / RWA registered name",
                    "autocomplete": "organization",
                }
            ),
            "contact_name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Principal or admin full name",
                    "autocomplete": "name",
                }
            ),
            "email": forms.EmailInput(
                attrs={"class": INPUT_CLASS, "placeholder": "school@example.com", "autocomplete": "email"}
            ),
            "address": forms.Textarea(
                attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Street, area, landmark"}
            ),
            "city": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "City", "autocomplete": "address-level2"}),
            "state": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "State", "autocomplete": "address-level1"}),
            "pincode": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "PIN / ZIP", "autocomplete": "postal-code"}),
            "student_count": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "placeholder": "Approx."}),
            "teacher_count": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "placeholder": "Approx."}),
            "branch_count": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "placeholder": "1 if single campus"}),
            "preferred_username": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Login username for your admin account",
                    "autocomplete": "username",
                }
            ),
            "website": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "https://yourschool.edu (optional)"}
            ),
            "affiliation_board": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "e.g. CBSE, ICSE, State Board, IB"}
            ),
            "school_motto": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Optional tagline"}),
            "affiliation_number": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "Board affiliation / registration no."}
            ),
            "landmark": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Nearby landmark"}),
            "district": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "District"}),
            "latitude": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "e.g. 17.3850", "inputmode": "decimal"}
            ),
            "longitude": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "e.g. 78.4867", "inputmode": "decimal"}
            ),
            "maps_url": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Google Maps link (optional)"}),
            "alternate_contact_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "alternate_contact_phone": forms.TextInput(attrs={"class": INPUT_CLASS, "inputmode": "tel"}),
            "admin_designation": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "e.g. Principal, Director, Owner"}
            ),
            "admin_profile_photo": forms.ClearableFileInput(attrs={"class": INPUT_CLASS}),
            "instruction_medium": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "sections_per_class_notes": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "e.g. 3 sections per class (optional)"}
            ),
            "curriculum_type": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Annual / Semester / Other"}),
            "total_classrooms": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
            "total_student_capacity": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
            "current_student_strength": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
            "non_teaching_staff_count": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0}),
            "current_erp_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "If you use an ERP today"}),
            "preferred_ui_language": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. English, తెలుగు"}),
            "expected_start_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "detailed_requirements": forms.Textarea(
                attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": "Longer notes or requirements (optional)"}
            ),
            "school_logo": forms.ClearableFileInput(attrs={"class": INPUT_CLASS}),
            "registration_certificate": forms.ClearableFileInput(attrs={"class": INPUT_CLASS}),
            "address_proof": forms.ClearableFileInput(attrs={"class": INPUT_CLASS}),
            "other_documents": forms.ClearableFileInput(attrs={"class": INPUT_CLASS}),
            "established_year": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1800, "max": 2100}),
        }
        labels = {
            "institution_name": "School name",
            "society_name": "Society / registered name",
            "contact_name": "Principal / admin name",
            "email": "School email",
            "address": "School address",
            "student_count": "Number of students",
            "teacher_count": "Number of teachers",
            "branch_count": "Branches / campuses",
            "preferred_username": "Username",
            "website": "School website",
            "affiliation_board": "Board / affiliation",
            "school_type": "School type",
            "established_year": "Established year",
            "school_motto": "School motto",
            "affiliation_number": "Affiliation number",
            "landmark": "Landmark",
            "district": "District",
            "latitude": "Latitude",
            "longitude": "Longitude",
            "maps_url": "Google Maps link",
            "alternate_contact_name": "Alternate contact name",
            "alternate_contact_phone": "Alternate contact phone",
            "admin_designation": "Admin designation",
            "admin_profile_photo": "Admin profile photo",
            "instruction_medium": "Medium of instruction",
            "sections_per_class_notes": "Sections per class",
            "curriculum_type": "Curriculum type",
            "total_classrooms": "Total classrooms",
            "lab_physics": "Physics lab",
            "lab_chemistry": "Chemistry lab",
            "lab_computer": "Computer lab",
            "has_library": "Library",
            "has_playground": "Playground",
            "has_transport": "Transport facility",
            "total_student_capacity": "Total student capacity",
            "current_student_strength": "Current student strength",
            "non_teaching_staff_count": "Non-teaching staff",
            "uses_erp": "Using an ERP today?",
            "current_erp_name": "Current ERP name",
            "require_data_migration": "Need data migration?",
            "preferred_ui_language": "Preferred UI language",
            "expected_start_date": "Expected start date",
            "detailed_requirements": "Detailed requirements",
            "school_logo": "School logo",
            "registration_certificate": "Registration certificate",
            "address_proof": "Address proof",
            "other_documents": "Other documents",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["institution_name"].required = True
        self.fields["society_name"].required = True
        self.fields["email"].required = True
        self.fields["contact_name"].required = True
        self.fields["preferred_username"].required = True
        self.fields["country_code"].required = True
        self.fields["phone_local"].required = True

        self.fields["website"].required = False
        self.fields["affiliation_board"].required = False

        st_choices = [
            ("", "—"),
            ("public", "Public"),
            ("private", "Private"),
            ("international", "International"),
        ]
        self.fields["school_type"] = forms.ChoiceField(
            label="School type",
            required=False,
            choices=st_choices,
            widget=forms.Select(attrs={"class": BS_INPUT}),
        )

        med_choices = [
            ("", "—"),
            ("english", "English"),
            ("telugu", "Telugu"),
            ("hindi", "Hindi"),
            ("other", "Other"),
        ]
        self.fields["instruction_medium"] = forms.ChoiceField(
            label="Medium of instruction",
            required=False,
            choices=med_choices,
            widget=forms.Select(attrs={"class": BS_INPUT}),
        )

        cur_choices = [
            ("", "—"),
            ("annual", "Annual"),
            ("semester", "Semester"),
            ("trimester", "Trimester"),
            ("other", "Other / mixed"),
        ]
        self.fields["curriculum_type"] = forms.ChoiceField(
            label="Curriculum type",
            required=False,
            choices=cur_choices,
            widget=forms.Select(attrs={"class": BS_INPUT}),
        )

        des_choices = [
            ("", "—"),
            ("principal", "Principal"),
            ("director", "Director"),
            ("owner", "Owner"),
            ("admin", "Administrator"),
            ("other", "Other"),
        ]
        self.fields["admin_designation"] = forms.ChoiceField(
            label="Admin designation",
            required=False,
            choices=des_choices,
            widget=forms.Select(attrs={"class": BS_INPUT}),
        )

        ui_lang_choices = [
            ("", "—"),
            ("en", "English"),
            ("te", "Telugu"),
            ("hi", "Hindi"),
        ]
        self.fields["preferred_ui_language"] = forms.ChoiceField(
            label="Preferred UI language",
            required=False,
            choices=ui_lang_choices,
            widget=forms.Select(attrs={"class": BS_INPUT}),
        )

        for fname, lbl in (
            ("lab_physics", "Physics lab available"),
            ("lab_chemistry", "Chemistry lab available"),
            ("lab_computer", "Computer lab available"),
            ("has_library", "Library on campus"),
            ("has_playground", "Playground"),
            ("has_transport", "Transport facility"),
            ("uses_erp", "Using another ERP"),
            ("require_data_migration", "Need data migration help"),
        ):
            self.fields[fname] = _enroll_tri_bool_field(lbl)

        for fn in (
            "school_motto",
            "affiliation_number",
            "landmark",
            "district",
            "latitude",
            "longitude",
            "maps_url",
            "alternate_contact_name",
            "alternate_contact_phone",
            "current_erp_name",
            "detailed_requirements",
        ):
            if fn in self.fields:
                self.fields[fn].required = False

        for fn in (
            "established_year",
            "total_classrooms",
            "total_student_capacity",
            "current_student_strength",
            "non_teaching_staff_count",
            "expected_start_date",
        ):
            if fn in self.fields:
                self.fields[fn].required = False

        for fn in (
            "admin_profile_photo",
            "school_logo",
            "registration_certificate",
            "address_proof",
            "other_documents",
        ):
            if fn in self.fields:
                self.fields[fn].required = False

    def clean_society_name(self):
        name = (self.cleaned_data.get("society_name") or "").strip()
        if not name:
            raise ValidationError("Enter the society or registered trust / RWA name.")
        return name

    def clean_website(self):
        w = (self.cleaned_data.get("website") or "").strip()
        if not w:
            return ""
        if not re.match(r"^https?://", w, re.I):
            w = "https://" + w
        try:
            URLValidator()(w)
        except ValidationError as exc:
            raise ValidationError("Enter a valid website URL (e.g. https://yourschool.edu).") from exc
        return w[:500]

    def clean_affiliation_board(self):
        return (self.cleaned_data.get("affiliation_board") or "").strip()[:120]

    def clean_streams_offered(self):
        raw = self.cleaned_data.get("streams_offered")
        if not raw:
            return ""
        if isinstance(raw, str):
            return raw.strip()[:200]
        return ",".join(raw)[:200]

    def clean_established_year(self):
        y = self.cleaned_data.get("established_year")
        if y in (None, ""):
            return None
        try:
            yi = int(y)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Enter a valid year.") from exc
        if yi < 1800 or yi > 2100:
            raise ValidationError("Enter a realistic established year.")
        return yi

    def clean_latitude(self):
        v = self.cleaned_data.get("latitude")
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return None
        try:
            d = Decimal(str(v).strip())
        except Exception as exc:
            raise ValidationError("Enter a valid latitude.") from exc
        if d < Decimal("-90") or d > Decimal("90"):
            raise ValidationError("Latitude must be between -90 and 90.")
        return d

    def clean_longitude(self):
        v = self.cleaned_data.get("longitude")
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return None
        try:
            d = Decimal(str(v).strip())
        except Exception as exc:
            raise ValidationError("Enter a valid longitude.") from exc
        if d < Decimal("-180") or d > Decimal("180"):
            raise ValidationError("Longitude must be between -180 and 180.")
        return d

    def clean_maps_url(self):
        u = (self.cleaned_data.get("maps_url") or "").strip()
        if not u:
            return ""
        if not re.match(r"^https?://", u, re.I):
            u = "https://" + u
        try:
            URLValidator()(u)
        except ValidationError as exc:
            raise ValidationError("Enter a valid maps URL (https://…).") from exc
        return u[:500]

    def clean_alternate_contact_phone(self):
        return (self.cleaned_data.get("alternate_contact_phone") or "").strip()[:40]

    def clean(self):
        cleaned = super().clean()
        if "phone_local" in self.errors or "country_code" in self.errors:
            return cleaned
        cc = (cleaned.get("country_code") or "+91").strip()
        local = re.sub(r"\D", "", cleaned.get("phone_local") or "")
        if not local:
            self.add_error("phone_local", ValidationError("Enter your mobile number (digits only)."))
        elif len(local) < 8:
            self.add_error("phone_local", ValidationError("That number looks too short."))
        elif len(local) > 15:
            self.add_error("phone_local", ValidationError("That number looks too long."))
        else:
            cc_digits = re.sub(r"\D", "", cc)
            combined = f"+{cc_digits}{local}" if cc_digits else local
            cleaned["phone"] = combined[:30]
        return cleaned

    def clean_intended_plan(self):
        raw = (self.cleaned_data.get("intended_plan") or "premium").strip().lower()
        if raw == "monthly":
            raw = "basic"
        # Legacy UI / audit values → Basic / Pro / Premium (super_admin.Plan)
        legacy = {
            "core": "basic",
            "advance": "pro",
            "standard": "pro",
            "enterprise": "premium",
            "yearly": "basic",
            "trial": "premium",
        }
        if raw in legacy:
            raw = legacy[raw]
        allowed = frozenset({"basic", "pro", "premium"})
        if raw not in allowed:
            raise ValidationError("Invalid plan selection.")
        return raw

    def clean_preferred_username(self):
        u = (self.cleaned_data.get("preferred_username") or "").strip()
        if not u:
            return u
        UserModel = get_user_model()
        if UserModel.objects.filter(username=u).exists():
            raise ValidationError(
                "This username is already taken. Choose another or sign in if you already have an account."
            )
        return u

    def clean_password2(self):
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 is None or p2 is None:
            return p2
        if p1 != p2:
            raise ValidationError("The two password fields do not match.")
        # Enrollment: skip "too common" check; keep other AUTH_PASSWORD_VALIDATORS (e.g. length).
        enrollment_validators = [
            v
            for v in get_default_password_validators()
            if not isinstance(v, CommonPasswordValidator)
        ]
        validate_password(p2, user=None, password_validators=enrollment_validators)
        return p2

    def save(self, commit=True):
        enrollment = super().save(commit=False)
        enrollment.pending_password_hash = make_password(self.cleaned_data["password2"])
        if commit:
            enrollment.save()
        return enrollment


class SuperAdminEnrollmentProvisionForm(forms.Form):
    """Super admin: product tier when provisioning a new tenant (Starter / Enterprise, or trial)."""

    BILLING_TIER_CHOICES = [
        ("trial", "Trial (14 days) — Starter modules, then Starter or Enterprise"),
        ("starter", "Starter — ₹39 per student / month"),
        ("enterprise", "Enterprise — ₹59 per student / month"),
    ]
    billing_tier = forms.ChoiceField(
        choices=BILLING_TIER_CHOICES,
        initial="trial",
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )


class SuperAdminEnrollmentDeclineForm(forms.Form):
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Optional message to keep internally"}),
    )


class SchoolExamSingleForm(forms.Form):
    """Admin: one exam session + one subject paper (one class, one section, one date)."""

    name = forms.CharField(
        max_length=100,
        label="Exam session name",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Annual Exam 2026"}),
    )
    class_name = forms.ChoiceField(choices=[], widget=forms.Select(attrs={"class": BS_INPUT, "id": "id_single_class"}))
    section = forms.ChoiceField(choices=[], widget=forms.Select(attrs={"class": BS_INPUT, "id": "id_single_section"}))
    subject = forms.ModelChoiceField(
        queryset=Subject.objects.none(),
        widget=forms.Select(attrs={"class": BS_INPUT, "id": "id_single_subject"}),
    )
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS, "id": "id_single_date"}))
    start_time = forms.TimeField(
        required=False,
        widget=forms.TimeInput(attrs={"type": "time", "class": INPUT_CLASS, "id": "id_single_start_time"}),
    )
    end_time = forms.TimeField(
        required=False,
        widget=forms.TimeInput(attrs={"type": "time", "class": INPUT_CLASS, "id": "id_single_end_time"}),
    )
    room = forms.CharField(
        required=False,
        max_length=120,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Room 102 / Main Hall"}),
    )
    details = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Optional instructions / notes"}),
    )
    topics = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Optional topics covered"}),
    )
    total_marks = forms.IntegerField(min_value=1, max_value=1000, initial=100, widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1}))
    teacher = forms.TypedChoiceField(choices=[], required=False, empty_value=None, widget=forms.Select(attrs={"class": BS_INPUT}))

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        classrooms = ClassRoom.objects.select_related("academic_year").order_by(*ORDER_AY_START_GRADE_NAME)
        self.fields["class_name"].choices = [("", "Select Class")] + [(c.name, c.name) for c in classrooms]
        self.fields["section"].choices = [("", "Select Section")] + [(s.name, s.name) for s in Section.objects.order_by("name")]
        self.fields["subject"].queryset = Subject.objects.order_by("display_order", "name")
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

    def clean(self):
        data = super().clean()
        st = data.get("start_time")
        et = data.get("end_time")
        if (st and not et) or (et and not st):
            raise forms.ValidationError("Set both start and end time, or leave both empty for an all-day paper.")
        if st and et and et <= st:
            raise forms.ValidationError("End time must be after start time.")
        return data


class SchoolExamEditForm(forms.ModelForm):
    """Admin: edit exam including optional time range for the calendar."""

    class Meta:
        model = Exam
        fields = [
            "name",
            "date",
            "start_time",
            "end_time",
            "room",
            "details",
            "topics",
            "class_name",
            "section",
            "subject",
            "total_marks",
            "teacher",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Exam title"}),
            "date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "start_time": forms.TimeInput(attrs={"type": "time", "class": INPUT_CLASS}),
            "end_time": forms.TimeInput(attrs={"type": "time", "class": INPUT_CLASS}),
            "room": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Optional room / hall"}),
            "details": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Optional instructions / notes"}),
            "topics": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Optional topics covered"}),
            "total_marks": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "max": 1000}),
        }

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django import forms as dforms

        classrooms = ClassRoom.objects.select_related("academic_year").order_by(*ORDER_AY_START_GRADE_NAME)
        self.fields["class_name"] = dforms.ChoiceField(
            choices=[(c.name, c.name) for c in classrooms],
            widget=dforms.Select(attrs={"class": BS_INPUT}),
        )
        self.fields["section"] = dforms.ChoiceField(
            choices=[(s.name, s.name) for s in Section.objects.order_by("name")],
            widget=dforms.Select(attrs={"class": BS_INPUT}),
        )
        self.fields["subject"].queryset = Subject.objects.order_by("display_order", "name")
        self.fields["subject"].required = False
        self.fields["subject"].widget.attrs.update({"class": BS_INPUT})
        self.fields["teacher"].queryset = Teacher.objects.filter(user__school=school).select_related("user").order_by(
            "user__first_name", "user__last_name"
        )
        self.fields["teacher"].required = False
        self.fields["teacher"].widget.attrs.update({"class": BS_INPUT})

    def clean(self):
        data = super().clean()
        st = data.get("start_time")
        et = data.get("end_time")
        if st and et and et <= st:
            raise forms.ValidationError("End time must be after start time.")
        if (st and not et) or (et and not st):
            raise forms.ValidationError("Set both start and end time, or leave both empty for an all-day exam.")
        return data


class SchoolExamSchedulerForm(forms.Form):
    """Admin: multi-class, multi-section, multi-subject with a manual date per subject."""

    exam_name = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "e.g. Mid Term",
                "id": "id_scheduler_exam_name",
            }
        ),
        help_text="Creates one session per class–section; each subject becomes a dated paper under that session.",
    )
    total_marks = forms.IntegerField(
        min_value=1,
        max_value=1000,
        initial=100,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "id": "id_scheduler_total_marks"}),
    )

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)


class AttendanceForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ["student", "date", "status"]
        widgets = {
            "student": forms.Select(attrs={"class": INPUT_CLASS}),
            "date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "status": forms.Select(attrs={"class": INPUT_CLASS}),
        }


def _normalize_student_gender_to_db(value) -> str:
    """
    Student.gender is CharField(max_length=1) with codes M/F/O.
    Master-data dropdowns submit full labels (e.g. "Male"); map them before save.
    """
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    if len(raw) == 1:
        u = raw.upper()
        if u in ("M", "F", "O"):
            return u
    low = raw.lower()
    if low in ("f", "female", "girl") or low.startswith("female"):
        return "F"
    if low in ("m", "male", "boy") or low.startswith("male"):
        return "M"
    if low in ("o", "other") or "non-binary" in low or "nonbinary" in low:
        return "O"
    return ""


class _SectionSelectWithClassrooms(forms.Select):
    """
    Section <option> tags get data-classrooms="1,2,..." so student_add.html JS can
    filter by class without iterating a ModelChoice queryset (which hits the DB).
    """

    def __init__(self, section_to_classroom_csv: dict[str, str], *, attrs=None):
        super().__init__(attrs=attrs)
        self.section_to_classroom_csv = section_to_classroom_csv

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        if value not in (None, ""):
            csv = self.section_to_classroom_csv.get(str(value))
            if csv:
                option.setdefault("attrs", {})["data-classrooms"] = csv
        return option


# ---- School Admin: Student Add Form ----
class StudentAddForm(forms.Form):
    # Student Basic Info
    first_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Rahul"}),
    )
    middle_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Kumar"}),
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Sharma"}),
    )
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={
                "class": f"{INPUT_CLASS} student-dob-flatpickr",
                "placeholder": "DD/MM/YYYY",
                "autocomplete": "bday",
                "inputmode": "numeric",
            },
        ),
    )
    gender = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT, "data-master-key": "gender"}),
    )
    blood_group = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT, "data-master-key": "blood_group"}),
    )
    id_number = forms.CharField(
        max_length=50,
        required=False,
        label="Aadhar / ID Number",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Aadhar / National ID"}),
    )
    nationality = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT, "data-master-key": "nationality"}),
    )
    religion = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT, "data-master-key": "religion"}),
    )
    mother_tongue = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT, "data-master-key": "mother_tongue"}),
    )
    profile_image = forms.ImageField(
        required=False,
        label="Student photo",
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )

    # Academic Details (ChoiceField + materialized choices: avoids ModelChoiceIterator /
    # server-side cursor during template render when search_path can be wrong mid-request.)
    academic_year = forms.ChoiceField(
        choices=[("", "— Infer from class —")],
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )
    admission_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
    )
    classroom = forms.ChoiceField(
        choices=[("", "— Select class —")],
        required=True,
        widget=forms.Select(attrs={"class": BS_INPUT, "id": "id_classroom"}),
    )
    section = forms.ChoiceField(
        choices=[("", "— Select section —")],
        required=True,
        widget=forms.Select(attrs={"class": BS_INPUT, "id": "id_section"}),
    )
    roll_number = forms.CharField(
        max_length=50,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. 23"}),
    )
    admission_number = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "Auto-generated if empty",
                "style": "text-transform: uppercase",
                "autocomplete": "off",
            }
        ),
    )
    registration_number = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Registration no (optional)"}),
    )
    course_branch = forms.CharField(
        max_length=120,
        required=False,
        label="Course / Branch",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. MPC / BSc CS / BCom"}),
    )
    semester_year = forms.CharField(
        max_length=60,
        required=False,
        label="Semester / Year",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Sem 1 / Year 2"}),
    )
    previous_institution = forms.CharField(
        max_length=200,
        required=False,
        label="Previous school name",
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "placeholder": "School where the student studied before joining here",
            }
        ),
    )
    previous_school_academic_year = forms.CharField(
        max_length=80,
        required=False,
        label="Academic year (at previous school)",
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASS, "placeholder": "e.g. 2023–24 or 2023–2024"},
        ),
    )
    previous_grade_completed = forms.CharField(
        max_length=120,
        required=False,
        label="Grade / class completed (elsewhere)",
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASS, "placeholder": "e.g. Grade 9 / Class IX"},
        ),
    )
    previous_board = forms.CharField(
        max_length=120,
        required=False,
        label="Board / medium (optional)",
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASS, "placeholder": "e.g. CBSE, State, ICSE"},
        ),
    )
    previous_marks = forms.CharField(
        max_length=80,
        required=False,
        label="Overall % or CGPA (previous school)",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. 92% or 8.6 CGPA"}),
    )
    previous_marks_breakdown = forms.CharField(
        required=False,
        max_length=4000,
        label="Subject-wise marks & remarks",
        widget=forms.Textarea(
            attrs={
                "class": INPUT_CLASS,
                "rows": 4,
                "placeholder": "Optional: subject-wise marks, total, percentage, or other academic details from the previous school.",
            }
        ),
    )
    stream = forms.CharField(
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. MPC / BIPC / CEC"}),
    )
    student_type = forms.ChoiceField(
        choices=[
            ("", "— Not specified —"),
            ("REGULAR", "Regular"),
            ("TRANSFER", "Transfer"),
            ("SCHOLARSHIP", "Scholarship"),
        ],
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )

    # Parent Details
    father_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    father_mobile = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    father_occupation = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    mother_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    mother_mobile = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    mother_occupation = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    guardian_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    guardian_relation = forms.CharField(max_length=60, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    guardian_phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    parent_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Parent/Guardian name"}),
    )
    parent_phone = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. 9876543210"}),
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. parent@example.com"}),
        help_text="Optional. If provided, login credentials can be emailed.",
    )

    # Contact
    student_mobile = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    student_email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={"class": INPUT_CLASS}))
    address_line1 = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    address_line2 = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    city = forms.CharField(max_length=80, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    district = forms.CharField(max_length=80, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    state = forms.CharField(max_length=80, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    pincode = forms.CharField(max_length=12, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    country = forms.CharField(max_length=80, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "India"}))

    # Emergency / Medical
    emergency_contact_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    emergency_phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    allergies = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    medical_conditions = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    doctor_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    hospital = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    insurance_details = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))

    # Transport / Hostel
    transport_required = forms.ChoiceField(
        choices=[("NO", "No"), ("YES", "Yes")],
        required=False,
        widget=forms.RadioSelect,
        initial="NO",
    )
    route = forms.ModelChoiceField(queryset=Route.objects.none(), required=False, widget=forms.Select(attrs={"class": BS_INPUT}))
    pickup_point = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    hostel_required = forms.ChoiceField(
        choices=[("NO", "No"), ("YES", "Yes")],
        required=False,
        widget=forms.RadioSelect,
        initial="NO",
    )
    hostel_room = forms.ModelChoiceField(queryset=HostelRoom.objects.none(), required=False, widget=forms.Select(attrs={"class": BS_INPUT}))

    # Financial / Billing preferences
    scholarship = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    discount_percent = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        decimal_places=2,
        max_digits=6,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "max": 100, "step": "0.01"}),
    )
    installment_type = forms.ChoiceField(
        choices=[
            ("", "— Not specified —"),
            ("MONTHLY", "Monthly"),
            ("QUARTERLY", "Quarterly"),
            ("HALF_YEARLY", "Half-yearly"),
            ("YEARLY", "Yearly"),
        ],
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )
    first_payment_amount = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "step": "0.01"}),
    )
    payment_due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}))

    # Documents upload
    doc_birth_certificate = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"}))
    doc_id_proof = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"}))
    doc_transfer_certificate = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"}))
    doc_previous_marks = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"}))
    doc_passport_photo = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"}))
    doc_parent_id = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"}))

    # Status
    record_status = forms.ChoiceField(
        choices=[
            ("ACTIVE", "Active"),
            ("INACTIVE", "Inactive"),
            ("TC_ISSUED", "TC Issued"),
            ("WITHDRAWN", "Withdrawn"),
        ],
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT}),
        initial="ACTIVE",
    )
    # Account Details (password optional - system generates if empty)
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": INPUT_CLASS, "placeholder": "Optional – system will generate"}
        ),
    )

    def __init__(self, school, *args, **kwargs):
        from django.db import connection

        self.school = school
        connection.set_tenant(school)
        super().__init__(*args, **kwargs)

        if getattr(self, "initial", None):
            for fname in ("academic_year", "classroom", "section"):
                val = self.initial.get(fname)
                if val is not None and hasattr(val, "pk"):
                    self.initial[fname] = str(val.pk)

        def _master_choices(master_key: str, empty_label: str = "— Select —"):
            qs = MasterDataOption.objects.filter(key=master_key, is_active=True).order_by("name")
            return [("", empty_label)] + [(o.name, o.name) for o in qs.only("name")]

        # Master dropdowns (tenant-scoped)
        self.fields["gender"].widget.choices = _master_choices("gender", "— Not specified —")
        self.fields["blood_group"].widget.choices = _master_choices("blood_group", "— Not specified —")
        self.fields["nationality"].widget.choices = _master_choices("nationality")
        self.fields["religion"].widget.choices = _master_choices("religion")
        self.fields["mother_tongue"].widget.choices = _master_choices("mother_tongue")
        # ORDER_AY_PK_GRADE_NAME includes "academic_year", which joins AcademicYear and can
        # SELECT wizard_settings; use grade-only ordering so missing AY table does not break GET.
        try:
            class_rows = list(
                ClassRoom.objects.order_by(*ORDER_GRADE_NAME).values("id", "name")
            )
        except Exception:
            class_rows = []
        self.fields["classroom"].choices = [("", "— Select class —")] + [
            (str(r["id"]), r["name"]) for r in class_rows
        ]
        try:
            sec_rows = list(Section.objects.order_by("name").values("id", "name"))
        except Exception:
            sec_rows = []
        self.fields["section"].choices = [("", "— Select section —")] + [
            (str(r["id"]), r["name"]) for r in sec_rows
        ]
        sec_csv: dict[str, str] = {}
        try:
            through = ClassRoom.sections.through
            buckets: dict[int, list[str]] = {}
            for cid, sid in through.objects.values_list("classroom_id", "section_id"):
                buckets.setdefault(sid, []).append(str(cid))
            sec_csv = {str(sid): ",".join(sorted(set(ids))) for sid, ids in buckets.items()}
        except Exception:
            sec_csv = {}
        self.fields["section"].widget = _SectionSelectWithClassrooms(
            sec_csv,
            attrs={"class": BS_INPUT, "id": "id_section", "required": True},
        )
        try:
            years = list(
                AcademicYear.objects.order_by("-start_date").only(
                    "pk", "name", "start_date", "end_date", "is_active"
                )
            )
        except Exception:
            years = []
        self.fields["academic_year"].choices = [("", "— Infer from class —")] + [
            (str(y.pk), str(y)) for y in years
        ]
        self.fields["route"].queryset = Route.objects.order_by("name")
        self.fields["hostel_room"].queryset = HostelRoom.objects.select_related("hostel").order_by("hostel__name", "room_number")

    def clean_academic_year(self):
        raw = self.cleaned_data.get("academic_year")
        if raw in (None, ""):
            return None
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            raise forms.ValidationError("Select a valid academic year.")
        try:
            return AcademicYear.objects.get(pk=pk)
        except AcademicYear.DoesNotExist:
            raise forms.ValidationError("Select a valid academic year.")

    def clean_classroom(self):
        raw = self.cleaned_data.get("classroom")
        if raw in (None, ""):
            raise forms.ValidationError("Select a class.")
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            raise forms.ValidationError("Select a valid class.")
        try:
            return ClassRoom.objects.get(pk=pk)
        except ClassRoom.DoesNotExist:
            raise forms.ValidationError("Select a valid class.")

    def clean_section(self):
        raw = self.cleaned_data.get("section")
        if raw in (None, ""):
            raise forms.ValidationError("Select a section.")
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            raise forms.ValidationError("Select a valid section.")
        try:
            return Section.objects.get(pk=pk)
        except Section.DoesNotExist:
            raise forms.ValidationError("Select a valid section.")

    def clean_parent_phone(self):
        phone = self.cleaned_data.get("parent_phone")
        if phone and not phone.replace(" ", "").isdigit():
            raise forms.ValidationError("Phone must contain only digits.")
        if phone and len(phone.replace(" ", "")) != 10:
            raise forms.ValidationError("Phone must be 10 digits.")
        return phone

    def clean_father_mobile(self):
        phone = self.cleaned_data.get("father_mobile")
        if phone and not phone.replace(" ", "").isdigit():
            raise forms.ValidationError("Phone must contain only digits.")
        return phone

    def clean_mother_mobile(self):
        phone = self.cleaned_data.get("mother_mobile")
        if phone and not phone.replace(" ", "").isdigit():
            raise forms.ValidationError("Phone must contain only digits.")
        return phone

    def clean_guardian_phone(self):
        phone = self.cleaned_data.get("guardian_phone")
        if phone and not phone.replace(" ", "").isdigit():
            raise forms.ValidationError("Phone must contain only digits.")
        return phone

    def clean_student_mobile(self):
        phone = self.cleaned_data.get("student_mobile")
        if phone and not phone.replace(" ", "").isdigit():
            raise forms.ValidationError("Phone must contain only digits.")
        return phone

    def clean_emergency_phone(self):
        phone = self.cleaned_data.get("emergency_phone")
        if phone and not phone.replace(" ", "").isdigit():
            raise forms.ValidationError("Phone must contain only digits.")
        return phone

    def clean_gender(self):
        return _normalize_student_gender_to_db(self.cleaned_data.get("gender"))

    def clean_admission_number(self):
        adm = self.cleaned_data.get("admission_number")
        if adm is None:
            return ""
        adm = adm.strip().upper()
        if not adm:
            return ""
        if User.objects.filter(username=adm).exists():
            raise forms.ValidationError("Admission Number already exists.")
        if Student.objects.filter(admission_number=adm).exists():
            raise forms.ValidationError("Admission Number already exists for this school.")
        return adm

    def clean(self):
        data = super().clean()
        section = data.get("section")
        classroom = data.get("classroom")
        if section and classroom and section not in classroom.sections.all():
            raise forms.ValidationError("Section must belong to selected class.")
        return data


# ---- School Admin: Student Edit Form ----
class StudentEditForm(forms.Form):
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={"class": INPUT_CLASS}))
    classroom = forms.ModelChoiceField(queryset=ClassRoom.objects.none(), required=False, widget=forms.Select(attrs={"class": BS_INPUT}))
    section = forms.ModelChoiceField(queryset=Section.objects.none(), required=False, widget=forms.Select(attrs={"class": BS_INPUT}))
    roll_number = forms.CharField(max_length=50, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    admission_number = forms.CharField(max_length=50, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    date_of_birth = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}))
    gender = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT, "data-master-key": "gender"}),
    )
    parent_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    parent_phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))

    def __init__(self, school, student=None, data=None, initial=None, **kwargs):
        super().__init__(data=data, initial=initial, **kwargs)
        self.school = school
        self.student = student
        self.fields["classroom"].queryset = ClassRoom.objects.select_related("academic_year").order_by(*ORDER_AY_PK_GRADE_NAME)
        self.fields["section"].queryset = Section.objects.order_by("name")
        qs = MasterDataOption.objects.filter(key="gender", is_active=True).order_by("name")
        self.fields["gender"].widget.choices = [("", "— Not specified —")] + [(o.name, o.name) for o in qs.only("name")]

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if not email:
            return email
        qs = User.objects.filter(email__iexact=email)
        if self.student and self.student.user_id:
            qs = qs.exclude(pk=self.student.user_id)
        if qs.exists():
            raise forms.ValidationError("This email is already in use.")
        return email

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

    def clean_gender(self):
        return _normalize_student_gender_to_db(self.cleaned_data.get("gender"))

    def clean(self):
        data = super().clean()
        section = data.get("section")
        classroom = data.get("classroom")
        if section and classroom and section not in classroom.sections.all():
            raise forms.ValidationError("Section must belong to selected class.")
        return data


# ---- School Admin: Student Master Edit Form (Enterprise) ----
class StudentMasterEditForm(StudentAddForm):
    """
    Reuses the admission fields for editing the student master record.
    Most fields are optional and stored in Student.extra_data.
    """

    def __init__(self, school, student=None, *args, **kwargs):
        self.student = student
        super().__init__(school, *args, **kwargs)

    def clean_admission_number(self):
        adm = (self.cleaned_data.get("admission_number") or "").strip().upper()
        if not adm:
            # Keep existing if left blank during edit.
            return ""
        # Admission number doubles as username; must be unique.
        user_qs = User.objects.filter(username=adm)
        if self.student and getattr(self.student, "user_id", None):
            user_qs = user_qs.exclude(pk=self.student.user_id)
        if user_qs.exists():
            raise forms.ValidationError("Admission Number already exists.")
        stud_qs = Student.objects.filter(admission_number=adm)
        if self.student:
            stud_qs = stud_qs.exclude(pk=self.student.pk)
        if stud_qs.exists():
            raise forms.ValidationError("Admission Number already exists for this school.")
        return adm


# ---- Student Self-Service: Profile Form ----
class StudentProfileForm(forms.Form):
    """
    Student-facing profile form.
    - Shows a safe, editable subset of fields.
    - Does NOT include critical academic/system identifiers.
    """

    # Locked identifiers (visible but not editable)
    username = forms.CharField(
        required=False,
        label="Username",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS}),
    )
    admission_number = forms.CharField(
        required=False,
        label="Admission No",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS}),
    )
    roll_number = forms.CharField(
        required=False,
        label="Roll No",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS}),
    )
    classroom = forms.CharField(
        required=False,
        label="Class",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS}),
    )
    section = forms.CharField(
        required=False,
        label="Section",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS}),
    )
    academic_year = forms.CharField(
        required=False,
        label="Academic year",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS}),
    )

    # Personal info (User + Student)
    first_name = forms.CharField(
        max_length=150,
        required=False,
        label="First name",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS}),
    )
    last_name = forms.CharField(
        max_length=150,
        required=False,
        label="Last name",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS}),
    )

    # Editable core fields (Student model)
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={
                "class": f"{INPUT_CLASS} student-dob-flatpickr",
                "placeholder": "DD/MM/YYYY",
                "autocomplete": "bday",
                "inputmode": "numeric",
            },
        ),
    )
    gender = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )
    student_mobile = forms.CharField(
        max_length=20,
        required=False,
        label="Phone",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. 9876543210"}),
    )
    address_line1 = forms.CharField(
        max_length=200,
        required=False,
        label="Address line 1",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "House no, street"}),
    )
    address_line2 = forms.CharField(
        max_length=200,
        required=False,
        label="Address line 2",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Area, landmark (optional)"}),
    )
    city = forms.CharField(max_length=80, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    district = forms.CharField(max_length=80, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    state = forms.CharField(max_length=80, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    pincode = forms.CharField(max_length=12, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    country = forms.CharField(
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "India"}),
    )
    profile_image = forms.ImageField(
        required=False,
        label="Student photo",
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )

    # Optional personal/parent details (stored in Student.extra_data + a few top-level fields)
    blood_group = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT, "data-master-key": "blood_group"}),
    )
    id_number = forms.CharField(
        max_length=50,
        required=False,
        label="Aadhar / ID Number",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Aadhar / National ID"}),
    )
    nationality = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT, "data-master-key": "nationality"}),
    )
    religion = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT, "data-master-key": "religion"}),
    )
    mother_tongue = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.Select(attrs={"class": BS_INPUT, "data-master-key": "mother_tongue"}),
    )

    father_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    father_mobile = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    father_occupation = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    mother_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    mother_mobile = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    mother_occupation = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    guardian_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    guardian_relation = forms.CharField(max_length=60, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    guardian_phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))

    parent_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    parent_phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    student_email = forms.EmailField(
        required=False,
        label="Alternate email",
        widget=forms.EmailInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. student@example.com"}),
    )
    email = forms.EmailField(
        required=False,
        label="Email",
        widget=forms.EmailInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. you@example.com"}),
        help_text="This updates your account email.",
    )

    emergency_contact_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    emergency_phone = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    allergies = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    medical_conditions = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    doctor_name = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    hospital = forms.CharField(max_length=150, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    insurance_details = forms.CharField(max_length=200, required=False, widget=forms.TextInput(attrs={"class": INPUT_CLASS}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        def _master_choices(master_key: str, empty_label: str = "— Select —"):
            qs = MasterDataOption.objects.filter(key=master_key, is_active=True).order_by("name")
            return [("", empty_label)] + [(o.name, o.name) for o in qs.only("name")]

        # Student.gender stores codes (M/F/O). Use model choices so the saved value
        # is also the displayed value after refresh.
        from apps.school_data.models import Student
        self.fields["gender"].widget.choices = [("", "— Not specified —")] + list(Student.Gender.choices)
        self.fields["blood_group"].widget.choices = _master_choices("blood_group", "— Not specified —")
        self.fields["nationality"].widget.choices = _master_choices("nationality")
        self.fields["religion"].widget.choices = _master_choices("religion")
        self.fields["mother_tongue"].widget.choices = _master_choices("mother_tongue")

        # Lock identifiers (students must not change these)
        for fname in ("username", "admission_number", "roll_number", "classroom", "section", "academic_year"):
            if fname in self.fields:
                self.fields[fname].disabled = True
                self.fields[fname].required = False

        # Security: students must NOT change phone themselves (school messaging integrity).
        self.fields["student_mobile"].disabled = True
        self.fields["student_mobile"].required = False
        self.fields["student_mobile"].help_text = "Phone is managed by the school administration."

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if not email:
            return ""
        from apps.accounts.models import User

        qs = User.objects.filter(email__iexact=email)
        # caller can pass request_user_id via initial for uniqueness exclusion
        req_uid = self.initial.get("_request_user_id")
        if req_uid:
            qs = qs.exclude(pk=req_uid)
        if qs.exists():
            raise forms.ValidationError("This email is already in use.")
        return email

    def clean_student_mobile(self):
        # Disabled field still flows through cleaned_data; never trust it for student self-service.
        return self.initial.get("student_mobile") or ""

    def clean_parent_phone(self):
        phone = self.cleaned_data.get("parent_phone")
        if phone and not str(phone).replace(" ", "").isdigit():
            raise forms.ValidationError("Phone must contain only digits.")
        return phone

    def clean_father_mobile(self):
        phone = self.cleaned_data.get("father_mobile")
        if phone and not str(phone).replace(" ", "").isdigit():
            raise forms.ValidationError("Phone must contain only digits.")
        return phone

    def clean_mother_mobile(self):
        phone = self.cleaned_data.get("mother_mobile")
        if phone and not str(phone).replace(" ", "").isdigit():
            raise forms.ValidationError("Phone must contain only digits.")
        return phone

    def clean_guardian_phone(self):
        phone = self.cleaned_data.get("guardian_phone")
        if phone and not str(phone).replace(" ", "").isdigit():
            raise forms.ValidationError("Phone must contain only digits.")
        return phone

    def clean_emergency_phone(self):
        phone = self.cleaned_data.get("emergency_phone")
        if phone and not str(phone).replace(" ", "").isdigit():
            raise forms.ValidationError("Phone must contain only digits.")
        return phone

    def clean_gender(self):
        return _normalize_student_gender_to_db(self.cleaned_data.get("gender"))


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
        self.fields["subjects"].queryset = Subject.objects.order_by("display_order", "name")
        self.fields["classrooms"].queryset = ClassRoom.objects.select_related("academic_year").order_by(
            *ORDER_AY_PK_GRADE_NAME
        )

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
        self.fields["subjects"].queryset = Subject.objects.order_by("display_order", "name")
        self.fields["classrooms"].queryset = ClassRoom.objects.select_related("academic_year").order_by(*ORDER_AY_PK_GRADE_NAME)
        if teacher:
            self.fields["role"].initial = teacher.user.role

    def clean(self):
        data = super().clean()
        return data


# ---- Student Bulk Import (CSV) ----
class StudentBulkImportForm(forms.Form):
    csv_file = forms.FileField(
        label="CSV File",
        help_text="UTF-8 CSV. First row: exact header names below. Required columns must have a value on every data row.",
        widget=forms.FileInput(attrs={"class": INPUT_CLASS, "accept": ".csv"}),
    )


# ---- School Admin: Academic Year ----
class AcademicYearForm(forms.ModelForm):
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
        input_formats=["%Y-%m-%d"],
        help_text="First day of this academic year (term start).",
    )
    end_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
        input_formats=["%Y-%m-%d"],
        help_text="Last day of this academic year (inclusive).",
    )

    class Meta:
        model = AcademicYear
        fields = ["name", "start_date", "end_date", "is_active", "description"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "e.g. 2026-2027",
                    "autocomplete": "off",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": INPUT_CLASS,
                    "rows": 3,
                    "placeholder": "Optional internal notes for administrators…",
                }
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        help_texts = {
            "name": "Shown across the app (reports, fees, student records). Must be unique.",
            "description": "Optional notes visible to school staff on this record.",
            "is_active": "Default year for enrolments and filters. Saving as active automatically clears other active years.",
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            return name
        qs = AcademicYear.objects.filter(name__iexact=name)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("An academic year with this name already exists.")
        return name

    def clean(self):
        from datetime import date

        data = super().clean()
        start = data.get("start_date")
        end = data.get("end_date")
        if start and end and end <= start:
            raise forms.ValidationError("End date must be after start date.")
        return data


class HolidayEventForm(forms.ModelForm):
    """Holiday / closure entry for a school holiday calendar (calendar FK set by view). One calendar day per row."""

    holiday_date = forms.DateField(
        label="Holiday date",
        widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
    )

    class Meta:
        model = HolidayEvent
        fields = [
            "calendar",
            "name",
            "holiday_type",
            "holiday_date",
            "applies_to",
            "description",
            "recurring_yearly",
        ]
        widgets = {
            "calendar": forms.HiddenInput(),
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Independence Day"}),
            "holiday_type": forms.Select(attrs={"class": BS_INPUT}),
            "applies_to": forms.Select(attrs={"class": BS_INPUT}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2}),
            "recurring_yearly": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, academic_year=None, **kwargs):
        self.academic_year = academic_year
        super().__init__(*args, **kwargs)
        inst = getattr(self, "instance", None)
        if inst and getattr(inst, "pk", None) and inst.start_date:
            self.fields["holiday_date"].initial = inst.start_date

    def clean(self):
        data = super().clean()
        inst = getattr(self, "instance", None)
        if inst and getattr(inst, "pk", None):
            data.setdefault("calendar", inst.calendar)
        cal = data.get("calendar")
        d = data.get("holiday_date")
        ay = self.academic_year or (cal.academic_year if cal else None)
        if ay and d and (d < ay.start_date or d > ay.end_date):
            raise forms.ValidationError("Holiday date must fall within the selected academic year.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        d = self.cleaned_data.get("holiday_date")
        if d:
            obj.start_date = d
            obj.end_date = d
        if commit:
            obj.save()
        return obj


class WorkingSundayOverrideForm(forms.ModelForm):
    class Meta:
        model = WorkingSundayOverride
        fields = ["calendar", "work_date", "applies_to", "note"]
        widgets = {
            "calendar": forms.HiddenInput(),
            "work_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
            "applies_to": forms.Select(attrs={"class": BS_INPUT}),
            "note": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Optional reason"}),
        }

    def clean_work_date(self):
        d = self.cleaned_data.get("work_date")
        if d and d.weekday() != 6:
            raise forms.ValidationError("Only Sundays can be marked as working days.")
        return d


class PromoteStudentsFilterForm(forms.Form):
    from_year = forms.ModelChoiceField(queryset=AcademicYear.objects.none(), widget=forms.Select(attrs={"class": BS_INPUT}))
    to_year = forms.ModelChoiceField(queryset=AcademicYear.objects.none(), widget=forms.Select(attrs={"class": BS_INPUT}))
    classroom = forms.ModelChoiceField(queryset=ClassRoom.objects.none(), required=False, widget=forms.Select(attrs={"class": BS_INPUT}))
    section = forms.ModelChoiceField(queryset=Section.objects.none(), required=False, widget=forms.Select(attrs={"class": BS_INPUT}))

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        years = AcademicYear.objects.order_by("-start_date")
        self.fields["from_year"].queryset = years
        self.fields["to_year"].queryset = years
        self.fields["classroom"].queryset = ClassRoom.objects.select_related("academic_year").order_by(*ORDER_AY_PK_GRADE_NAME)
        self.fields["section"].queryset = Section.objects.order_by("name")


class PromoteStudentsActionForm(forms.Form):
    action = forms.ChoiceField(choices=StudentPromotion.Action.choices, widget=forms.Select(attrs={"class": BS_INPUT}))
    target_classroom = forms.ModelChoiceField(queryset=ClassRoom.objects.none(), required=False, widget=forms.Select(attrs={"class": BS_INPUT}))
    target_section = forms.ModelChoiceField(queryset=Section.objects.none(), required=False, widget=forms.Select(attrs={"class": BS_INPUT}))

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_classroom"].queryset = ClassRoom.objects.select_related("academic_year").order_by(*ORDER_AY_PK_GRADE_NAME)
        self.fields["target_section"].queryset = Section.objects.order_by("name")


# ---- School Admin: Class (Grade) ----
class ClassRoomForm(forms.ModelForm):
    class Meta:
        model = ClassRoom
        # grade_order is intentionally not exposed in UI; it auto-fills from name in ClassRoom.save()
        fields = ["name", "description", "capacity", "academic_year", "sections"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Grade 1, Grade 10"}),
            "description": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Optional description"}),
            "capacity": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "placeholder": "Optional"}),
            "academic_year": forms.Select(attrs={"class": BS_INPUT}),
            "sections": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, school, *args, **kwargs):
        from django.db import connections
        from django.db import connection as active_connection
        from django_tenants.utils import get_tenant_database_alias

        self.school = school
        self.db_alias = get_tenant_database_alias()
        # Make tenant-bound choice lists stable across GET/POST even if the connection
        # was recycled between requests (common with schema-per-tenant search_path).
        try:
            conn = connections[self.db_alias]
            try:
                conn.ensure_connection()
            except Exception:
                pass
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.set_tenant(school)
            except Exception:
                pass
        except Exception:
            pass
        super().__init__(*args, **kwargs)

        # As a second line of defense, bind the active/default connection too.
        # Some parts of the app use the default connection directly.
        try:
            active_connection.set_tenant(school)
        except Exception:
            pass

        # Always validate against real DB rows (tenant-scoped).
        # This prevents "Select a valid choice" errors caused by stale/empty choice lists.
        def _set_academic_year_qs():
            # Prefer explicit alias (stable in django-tenants setups).
            qs = AcademicYear.objects.using(self.db_alias).order_by("-start_date")
            self.fields["academic_year"].queryset = qs
            try:
                self.fields["academic_year"].empty_label = "— Optional —"
            except Exception:
                pass

        try:
            _set_academic_year_qs()
            # If it rendered empty due to a transient tenant bind issue, retry once.
            if self.fields["academic_year"].queryset.count() == 0:
                try:
                    # Re-bind both connections and retry.
                    conn = connections[self.db_alias]
                    conn.set_tenant(school)
                except Exception:
                    pass
                try:
                    active_connection.set_tenant(school)
                except Exception:
                    pass
                _set_academic_year_qs()
        except Exception:
            self.fields["academic_year"].queryset = AcademicYear.objects.none()
        try:
            self.fields["sections"].queryset = Section.objects.using(self.db_alias).order_by("name")
        except Exception:
            self.fields["sections"].queryset = Section.objects.none()

        # Consistent Bootstrap styling + inline invalid feedback.
        for name, field in self.fields.items():
            w = field.widget
            base = (w.attrs.get("class") or "").strip() or INPUT_CLASS
            if self.is_bound and name in self.errors:
                w.attrs["class"] = f"{base} is-invalid".strip()
            else:
                w.attrs["class"] = base

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


# ---- School Admin: Subject (master list only) ----
class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ["name", "code"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Mathematics, Physics"}),
            "code": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. MATH01 (unique)"}),
        }

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.fields["name"].required = True
        self.fields["code"].required = True
        for fname in ("name", "code"):
            w = self.fields[fname].widget
            base = (w.attrs.get("class") or "").strip() or INPUT_CLASS
            if self.is_bound and fname in self.errors:
                w.attrs["class"] = f"{base} is-invalid".strip()
            else:
                w.attrs["class"] = base

    def save(self, commit=True):
        from django.db.models import Max

        SubjectModel = self._meta.model
        obj = super().save(commit=False)
        if not obj.pk:
            m = SubjectModel.objects.aggregate(Max("display_order")).get("display_order__max") or 0
            obj.display_order = int(m) + 10
        if commit:
            obj.save()
        return obj


class AdminCouponForm(forms.ModelForm):
    """SuperAdmin coupon create/edit (public schema)."""

    class Meta:
        model = Coupon
        fields = [
            "code",
            "discount_type",
            "discount_value",
            "max_usage",
            "valid_from",
            "valid_to",
            "is_active",
        ]
        widgets = {
            "code": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. SAVE10"}),
            "discount_type": forms.Select(attrs={"class": BS_INPUT}),
            "discount_value": forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "0.01"}),
            "max_usage": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": "0"}),
            "valid_from": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "valid_to": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].help_text = "Unique code; stored case-insensitive for lookup."
        self.fields["max_usage"].help_text = "0 = unlimited uses."

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()


class SaaSPlatformPaymentForm(forms.ModelForm):
    """Superadmin: record money received from a school (SaaS subscription)."""

    class Meta:
        model = SaaSPlatformPayment
        fields = [
            "school",
            "subscription",
            "amount",
            "payment_date",
            "payment_method",
            "reference",
            "internal_receipt_no",
            "service_period_start",
            "service_period_end",
            "notes",
        ]
        widgets = {
            "school": forms.Select(attrs={"class": BS_INPUT}),
            "subscription": forms.Select(attrs={"class": BS_INPUT}),
            "amount": forms.NumberInput(
                attrs={
                    "class": f"{INPUT_CLASS} rounded-start-0",
                    "step": "0.01",
                    "min": "0.01",
                    "placeholder": "0.00",
                }
            ),
            "payment_date": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "payment_method": forms.Select(attrs={"class": BS_INPUT}),
            "reference": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Bank UTR, gateway txn id, cheque no., etc.",
                    "autocomplete": "off",
                }
            ),
            "internal_receipt_no": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "e.g. RCP-2026-0142",
                    "autocomplete": "off",
                }
            ),
            "service_period_start": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "service_period_end": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "notes": forms.Textarea(
                attrs={
                    "class": INPUT_CLASS,
                    "rows": 3,
                    "placeholder": "Anything else finance or support should know later…",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["school"].queryset = School.objects.exclude(schema_name="public").order_by("name")
        self.fields["school"].label = "School"
        self.fields["subscription"].required = False
        self.fields["subscription"].label = "Link to current subscription"
        self.fields["subscription"].help_text = "Optional. Ties this row to the school’s active plan record. Updates when you change school."
        self.fields["reference"].required = False
        self.fields["reference"].label = "Bank / gateway reference"
        self.fields["internal_receipt_no"].required = False
        self.fields["internal_receipt_no"].label = "Internal receipt no."
        self.fields["internal_receipt_no"].help_text = "Your own voucher number for audits and reconciliation."
        self.fields["service_period_start"].required = False
        self.fields["service_period_start"].label = "Service period from"
        self.fields["service_period_end"].required = False
        self.fields["service_period_end"].label = "Service period to"
        self.fields["service_period_start"].help_text = "Optional: which billing period this payment covers."
        self.fields["service_period_end"].help_text = "Leave blank if open-ended or same as start."
        self.fields["notes"].required = False
        self.fields["notes"].label = "Internal notes"

        school_pk = None
        if self.data and self.data.get("school"):
            school_pk = self.data.get("school")
        elif self.initial.get("school") is not None:
            sch = self.initial["school"]
            school_pk = sch.pk if hasattr(sch, "pk") else sch

        sub_filter = Q(is_current=True)
        if school_pk:
            sub_filter &= Q(school_id=school_pk)
        if self.instance.pk and getattr(self.instance, "subscription_id", None):
            sub_filter |= Q(pk=self.instance.subscription_id)
        self.fields["subscription"].queryset = (
            SchoolSubscription.objects.filter(sub_filter)
            .select_related("school", "plan")
            .distinct()
            .order_by("school__name")
        )

    def clean_amount(self):
        from decimal import Decimal

        v = self.cleaned_data.get("amount")
        if v is not None and v <= Decimal("0"):
            raise forms.ValidationError("Amount must be greater than zero.")
        return v

    def clean(self):
        data = super().clean()
        school = data.get("school")
        sub = data.get("subscription")
        if sub and school and sub.school_id != school.id:
            self.add_error("subscription", "This subscription belongs to a different school.")
        start = data.get("service_period_start")
        end = data.get("service_period_end")
        if start and end and end < start:
            self.add_error("service_period_end", "End date must be on or after the start date.")
        return data

# ---------- Fee & Billing ----------

class FeeTypeForm(forms.ModelForm):
    class Meta:
        model = FeeType
        fields = ["name", "code", "description", "is_active"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": f"{INPUT_CLASS} rounded-3", "placeholder": "e.g. Tuition, Sports, Transport"}
            ),
            "code": forms.TextInput(
                attrs={"class": f"{INPUT_CLASS} rounded-3", "placeholder": "Optional code (e.g. TUITION)"}
            ),
            "description": forms.Textarea(
                attrs={
                    "class": f"{INPUT_CLASS} rounded-3",
                    "rows": 3,
                    "placeholder": "Short description for staff (shown in master list)",
                }
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not getattr(self.instance, "pk", None):
            self.fields["is_active"].initial = True


class FeeStructureForm(forms.ModelForm):
    class Meta:
        model = FeeStructure
        fields = [
            "academic_year",
            "classroom",
            "section",
            "line_name",
            "fee_type",
            "amount",
            "frequency",
            "installments_enabled",
            "first_due_date",
            "due_day_of_month",
            "late_fine_rule",
            "discount_allowed",
            "is_active",
        ]
        widgets = {
            "academic_year": forms.Select(attrs={"class": BS_INPUT}),
            "classroom": forms.Select(attrs={"class": BS_INPUT}),
            "section": forms.Select(attrs={"class": BS_INPUT}),
            "line_name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Optional display name (defaults to fee type)",
                }
            ),
            "fee_type": forms.Select(attrs={"class": BS_INPUT}),
            "amount": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "step": "0.01"}),
            "frequency": forms.Select(attrs={"class": BS_INPUT}),
            "installments_enabled": forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"}),
            "first_due_date": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "due_day_of_month": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": 1, "max": 28, "placeholder": "e.g. 5"}
            ),
            "late_fine_rule": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "e.g. Standard late fee (see Late Fine Rules)",
                }
            ),
            "discount_allowed": forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input", "role": "switch"}),
        }

    def __init__(self, school, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fee_type"].queryset = FeeType.objects.filter(is_active=True).order_by("name")
        self.fields["classroom"].queryset = ClassRoom.objects.all().order_by(*ORDER_GRADE_NAME)
        self.fields["section"].queryset = Section.objects.all().order_by("name")
        self.fields["section"].required = False
        self.fields["academic_year"].queryset = AcademicYear.objects.order_by("-start_date")
        self.fields["due_day_of_month"].required = False
        self.fields["first_due_date"].required = False
        self.fields["line_name"].required = False
        self.fields["late_fine_rule"].required = False
        self.fields["classroom"].help_text = "Required. Dues are auto-created for all active students in this class."
        self.fields["section"].help_text = "Optional: limit to one section (e.g. Grade 5A only)."
        self.fields["first_due_date"].help_text = "If set, used as the due date for new student fee lines. Otherwise due day of month is used."
        self.fields["due_day_of_month"].help_text = "Used when first due date is empty."
        self.fields["installments_enabled"].help_text = "Optional flag for installment-eligible fee lines (reporting / future use)."
        self.fields["discount_allowed"].initial = True
        if not getattr(self.instance, "pk", None):
            self.fields["is_active"].initial = True

    def clean(self):
        data = super().clean()
        classroom = data.get("classroom")
        section = data.get("section")
        if section and not classroom:
            raise forms.ValidationError("Select a class before choosing a section.")
        if section and classroom and section not in classroom.sections.all():
            raise forms.ValidationError("Section must belong to the selected class.")
        return data

class PaymentForm(forms.ModelForm):
    PAYMENT_MODE_CHOICES = [
        ("Cash", "Cash"),
        ("UPI", "UPI"),
        ("Card", "Card"),
        ("Bank transfer", "Bank transfer"),
        ("Bank Transfer", "Bank transfer"),  # legacy stored value
        ("Online", "Online gateway"),
        ("Cheque", "Cheque"),
    ]

    payment_method = forms.ChoiceField(
        choices=PAYMENT_MODE_CHOICES,
        widget=forms.Select(attrs={"class": BS_INPUT}),
    )

    class Meta:
        model = Payment
        fields = [
            "amount",
            "payment_date",
            "payment_method",
            "receipt_number",
            "transaction_reference",
            "notes",
        ]
        labels = {
            "receipt_number": "Internal receipt no. (optional)",
            "transaction_reference": "Transaction / reference no. (optional)",
            "payment_method": "Payment mode",
        }
        widgets = {
            "amount": forms.NumberInput(
                attrs={
                    "class": INPUT_CLASS + " no-spinner",
                    "min": 0,
                    "step": "0.01",
                    "inputmode": "decimal",
                }
            ),
            "payment_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "type": "text",
                    "class": INPUT_CLASS + " fc-payment-datepicker",
                    "autocomplete": "off",
                    "placeholder": "Select date",
                },
            ),
            "receipt_number": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "Optional — internal receipt no."}
            ),
            "transaction_reference": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Optional — UPI / bank / gateway reference",
                }
            ),
            "notes": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2}),
        }

    def __init__(self, *args, fee=None, **kwargs):
        self._fee = fee
        super().__init__(*args, **kwargs)
        self.fields["receipt_number"].required = False
        self.fields["transaction_reference"].required = False
        self.fields["notes"].required = False
        pd_field = self.fields["payment_date"]
        prev = list(pd_field.input_formats) if pd_field.input_formats else []
        if "%Y-%m-%d" not in prev:
            pd_field.input_formats = ["%Y-%m-%d"] + prev

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is None or self._fee is None:
            return amount
        paid = self._fee.payments.aggregate(s=Sum("amount"))["s"] or Decimal("0")
        remaining = max(Decimal("0"), self._fee.effective_due_amount - paid)
        if amount <= 0:
            raise forms.ValidationError("Amount must be greater than zero.")
        if amount > remaining:
            raise forms.ValidationError(
                f"Amount cannot exceed balance due ({remaining})."
            )
        return amount


class PaymentHeaderForm(forms.Form):
    """Shared payment metadata for multi-line fee collection (no per-line amount)."""

    payment_date = forms.DateField(
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={
                "type": "text",
                "class": INPUT_CLASS + " fc-payment-datepicker",
                "autocomplete": "off",
                "placeholder": "Select date",
            },
        )
    )
    receipt_number = forms.CharField(
        required=False,
        label="Internal receipt no. (optional)",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Optional — internal receipt no."}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pd_field = self.fields["payment_date"]
        prev = list(pd_field.input_formats) if pd_field.input_formats else []
        if "%Y-%m-%d" not in prev:
            pd_field.input_formats = ["%Y-%m-%d"] + prev


class FeePaymentMetadataForm(forms.Form):
    """Edit receipt metadata for a recorded payment (batch or single-line)."""

    payment_date = forms.DateField(
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={
                "type": "text",
                "class": INPUT_CLASS + " fc-payment-datepicker",
                "autocomplete": "off",
                "placeholder": "Payment date",
            },
        )
    )
    receipt_number = forms.CharField(
        required=False,
        label="School voucher / internal receipt no.",
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASS, "placeholder": "Optional internal voucher number"}
        ),
    )
    transaction_reference = forms.CharField(
        required=False,
        label="Transaction reference",
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASS, "placeholder": "UPI / bank ref. (optional)"}
        ),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3, "placeholder": "Notes (optional)"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pd_field = self.fields["payment_date"]
        prev = list(pd_field.input_formats) if pd_field.input_formats else []
        if "%Y-%m-%d" not in prev:
            pd_field.input_formats = ["%Y-%m-%d"] + prev


class OrphanPaymentEditForm(FeePaymentMetadataForm):
    """Single-line (non-batch) payment: amount + voucher metadata."""

    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Amount (₹)",
        widget=forms.NumberInput(
            attrs={
                "class": INPUT_CLASS + " pe-no-spin",
                "step": "0.01",
                "min": "0.01",
                "inputmode": "decimal",
                "autocomplete": "off",
            }
        ),
    )

    def __init__(self, *args, max_amount: Decimal | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._max_amount = max_amount
        if max_amount is not None:
            self.fields["amount"].help_text = (
                f"Maximum ₹{max_amount} (cannot exceed balance due on this fee)."
            )
        from collections import OrderedDict

        order = [
            "amount",
            "payment_date",
            "receipt_number",
            "transaction_reference",
            "notes",
        ]
        self.fields = OrderedDict((k, self.fields[k]) for k in order if k in self.fields)

    def clean_amount(self):
        amt = self.cleaned_data["amount"]
        if self._max_amount is not None and amt > self._max_amount:
            raise ValidationError(
                f"Amount cannot exceed ₹{self._max_amount} (balance due on this fee line)."
            )
        return amt


class FeeConcessionForm(forms.ModelForm):
    """Per fee line: fixed + % concession and category (scholarship, sibling, etc.)."""

    class Meta:
        model = Fee
        fields = ["concession_fixed", "concession_percent", "concession_kind", "concession_note"]
        labels = {
            "concession_fixed": "Fixed discount (₹)",
            "concession_percent": "Percentage discount (%)",
            "concession_kind": "Concession type",
            "concession_note": "Remarks",
        }
        widgets = {
            "concession_fixed": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": 0, "step": "0.01"}
            ),
            "concession_percent": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "min": 0, "max": 100, "step": "0.01"}
            ),
            "concession_kind": forms.Select(attrs={"class": BS_INPUT}),
            "concession_note": forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2, "placeholder": "Optional note"}),
        }

    def clean_concession_percent(self):
        v = self.cleaned_data.get("concession_percent")
        if v is not None and (v < 0 or v > 100):
            raise forms.ValidationError("Enter a percentage between 0 and 100.")
        return v

    def clean_concession_fixed(self):
        v = self.cleaned_data.get("concession_fixed")
        if v is not None and v < 0:
            raise forms.ValidationError("Fixed discount cannot be negative.")
        return v

    def clean(self):
        cleaned = super().clean()
        inst = self.instance
        if not inst or not getattr(inst, "pk", None):
            return cleaned
        from decimal import ROUND_HALF_UP

        base = inst.amount or Decimal("0")
        pct = cleaned.get("concession_percent") or Decimal("0")
        fixed = cleaned.get("concession_fixed") or Decimal("0")
        pct_amt = (base * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if pct_amt + fixed > base:
            raise forms.ValidationError("Total concession cannot exceed the original fee amount.")
        return cleaned


class FeeLineAdjustForm(forms.ModelForm):
    """Staff adjustment of billed amount and due date on an assigned student fee line."""

    class Meta:
        model = Fee
        fields = ["amount", "due_date"]
        labels = {"amount": "Billed amount (₹)", "due_date": "Due date"}
        widgets = {
            "amount": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 0, "step": "0.01"}),
            "due_date": forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}),
        }

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is None or not getattr(self.instance, "pk", None):
            return amount
        paid = self.instance.payments.aggregate(s=Sum("amount"))["s"] or Decimal("0")
        if amount < paid:
            raise forms.ValidationError(
                f"Billed amount cannot be less than total payments already recorded (₹{paid})."
            )
        return amount


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
        self.fields["applied_class"].queryset = ClassRoom.objects.all().order_by(*ORDER_GRADE_NAME)


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
