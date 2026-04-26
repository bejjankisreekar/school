"""
Tenant-schema models for School ERP. Each school has its own PostgreSQL schema (django-tenants).

Row isolation is by schema, not by a company_id/school_id column: queries run inside
``connection.set_tenant(school)`` / ``tenant_context(school)`` only see that school's rows.
Public-schema models (e.g. customers.School, accounts.User.school) identify which tenant applies.

User FKs to accounts.User are allowed (User rows live in the public schema).
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import models, transaction
from django.db.models import Q

# BaseModel for audit - uses accounts.User (public schema)
from apps.core.models import BaseModel


class MasterDataOption(BaseModel):
    """
    Tenant-scoped master data options for dropdowns (gender, department, etc.).

    Stored per-tenant (schema) so values are automatically school-specific.
    """

    class Key(models.TextChoices):
        GENDER = "gender", "Gender"
        BLOOD_GROUP = "blood_group", "Blood group"
        NATIONALITY = "nationality", "Nationality"
        RELIGION = "religion", "Religion"
        MOTHER_TONGUE = "mother_tongue", "Mother tongue"
        DESIGNATION = "designation", "Designation"
        DEPARTMENT = "department", "Department"
        QUALIFICATION = "qualification", "Qualification"
        CASTE_CATEGORY = "caste_category", "Caste / Category"
        MARITAL_STATUS = "marital_status", "Marital status"
        STAFF_TYPE = "staff_type", "Staff type"
        EMPLOYMENT_TYPE = "employment_type", "Employment type"
        SHIFT = "shift", "Shift"
        PAYROLL_CATEGORY = "payroll_category", "Payroll category"
        EXPERIENCE_LEVEL = "experience_level", "Experience level"
        REPORTING_MANAGER = "reporting_manager", "Reporting manager"
        RELATIONSHIP = "relationship", "Relationship"
        OCCUPATION = "occupation", "Occupation"
        ANNUAL_INCOME_RANGE = "annual_income_range", "Annual income range"
        EDUCATION_LEVEL = "education_level", "Education level"
        ADMISSION_SOURCE = "admission_source", "Admission source"
        FEE_CATEGORY = "fee_category", "Fee category"
        STUDENT_STATUS = "student_status", "Student status"
        PREVIOUS_BOARD = "previous_board", "Previous board"
        MEDIUM_OF_INSTRUCTION = "medium_of_instruction", "Medium of instruction"
        ATTENDANCE_STATUS = "attendance_status", "Attendance status"
        ADMISSION_STATUS = "admission_status", "Admission status"
        TRANSPORT_REQUIRED = "transport_required", "Transport required"
        STATUS = "status", "Status"

    key = models.CharField(max_length=40, choices=Key.choices, db_index=True)
    name = models.CharField(max_length=160, db_index=True)
    name_normalized = models.CharField(
        max_length=180,
        db_index=True,
        help_text="Lowercased + trimmed for duplicate prevention.",
    )
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["key", "display_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["key", "name_normalized"], name="uniq_masterdata_key_name_norm"),
        ]

    def save(self, *args, **kwargs):
        self.name = (self.name or "").strip()
        self.name_normalized = self.name.lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.key}: {self.name}"


class DropdownMaster(BaseModel):
    """
    Tenant-scoped master dropdown registry used by settings UI.

    This is a generic table that can back any dropdown across the ERP.
    """

    field_key = models.CharField(max_length=80, db_index=True)
    display_label = models.CharField(max_length=160, db_index=True)
    option_value = models.CharField(max_length=160, db_index=True)
    option_value_normalized = models.CharField(
        max_length=180,
        db_index=True,
        help_text="Lowercased + trimmed for duplicate prevention.",
    )
    category = models.CharField(max_length=60, db_index=True, blank=True, default="")
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["category", "field_key", "display_order", "display_label"]
        constraints = [
            models.UniqueConstraint(
                fields=["field_key", "option_value_normalized"],
                name="uniq_dropdownmaster_key_value_norm",
            ),
        ]

    def save(self, *args, **kwargs):
        self.field_key = (self.field_key or "").strip()
        self.display_label = (self.display_label or "").strip()
        self.option_value = (self.option_value or "").strip()
        self.option_value_normalized = (self.option_value or "").strip().lower()
        self.category = (self.category or "").strip()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.field_key}: {self.display_label}"


class AcademicYear(BaseModel):
    """Academic year (e.g. 2025-2026). When saving as active, other years are cleared first (app-level)."""
    name = models.CharField(max_length=50, db_index=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=False, db_index=True)
    description = models.TextField(
        blank=True,
        default="",
        help_text="Internal notes for staff (not shown on student-facing screens by default).",
    )
    wizard_settings = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured setup from the academic year wizard (terms, working days, copy flags, etc.).",
    )

    class Meta:
        ordering = ["start_date"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        # If DB still has legacy partial unique on is_active=True, we must clear others before INSERT.
        # Never .exclude(pk=self.pk) when pk is None — that can yield zero rows updated on some setups.
        with transaction.atomic():
            if self.is_active:
                qs = AcademicYear.objects.select_for_update().filter(is_active=True)
                if self.pk is not None:
                    qs = qs.exclude(pk=self.pk)
                qs.update(is_active=False)
            super().save(*args, **kwargs)


class Section(BaseModel):
    """Independent section (A, B, C, D, E). Reusable across classes."""
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ClassRoom(BaseModel):
    """Grade level (e.g. Grade 1, Grade 10). References sections via M2M."""
    name = models.CharField(max_length=50, db_index=True)
    grade_order = models.PositiveIntegerField(
        default=0,
        db_index=True,
        help_text="Sort position: lower = earlier grade (1 before 10). Filled from name if left 0.",
    )
    description = models.TextField(blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    active_schedule_profile = models.ForeignKey(
        "timetable.ScheduleProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_for_classrooms",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name="classrooms",
        null=True,
        blank=True,
    )
    sections = models.ManyToManyField(
        Section,
        related_name="classrooms",
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["name", "academic_year"],
                condition=Q(academic_year__isnull=False),
                name="unique_classroom_name_per_academic_year",
            ),
        ]
        ordering = ["academic_year", "grade_order", "name"]

    def save(self, *args, **kwargs):
        if self.grade_order == 0 and (self.name or "").strip():
            from apps.school_data.classroom_ordering import grade_order_from_name

            self.grade_order = grade_order_from_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Subject(BaseModel):
    """
    School-wide subject master (tenant-scoped). Not tied to class or teacher.
    Tenant = one school (PostgreSQL schema); no School FK on this model.

    Class + section + teacher mapping: ClassSectionSubjectTeacher (subject assignment).
    """

    name = models.CharField(max_length=100, db_index=True)
    code = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text="Short unique code, e.g. MATH01.",
    )
    display_order = models.PositiveIntegerField(
        default=0,
        db_index=True,
        help_text="Lower numbers appear first in subject pickers and the school subject list.",
    )

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self) -> str:
        return self.name


class Teacher(BaseModel):
    """Teacher profile linked to User."""

    class Gender(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"
        OTHER = "O", "Other"

    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="teacher_profile",
    )
    subjects = models.ManyToManyField(
        Subject,
        blank=True,
        related_name="teachers",
    )
    classrooms = models.ManyToManyField(
        ClassRoom,
        blank=True,
        related_name="assigned_teachers",
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teachers_legacy",
    )
    employee_id = models.CharField(max_length=50, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    qualification = models.CharField(max_length=200, blank=True)
    experience = models.CharField(max_length=100, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(
        max_length=1,
        choices=Gender.choices,
        blank=True,
        default="",
        db_index=True,
    )
    address = models.TextField(blank=True, null=True)
    profile_image = models.ImageField(upload_to="teacher_profiles/", blank=True, null=True)
    extra_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Extended profile: contact, professional, family, medical, payroll, etc.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["employee_id"],
                condition=Q(employee_id__gt=""),
                name="unique_employee_id_per_tenant",
            ),
        ]

    def __str__(self) -> str:
        return f"Teacher: {self.user.get_full_name() or self.user.username}"


class ClassSectionSubjectTeacher(BaseModel):
    """
    Subject assignment: which teacher teaches which master subject for a class+section.
    (ERP “subject assignment” table.) One teacher per (class, section, subject).
    """

    class_obj = models.ForeignKey(
        ClassRoom,
        on_delete=models.CASCADE,
        related_name="class_section_subject_teacher_mappings",
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.CASCADE,
        related_name="class_section_subject_teacher_mappings",
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name="class_section_subject_teacher_mappings",
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="class_section_subject_teacher_mappings",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["class_obj", "section", "subject"],
                name="unique_class_section_subject_teacher",
            ),
        ]

    def __str__(self) -> str:
        # Keep it readable in the admin + debug logs.
        teacher_name = self.teacher.user.get_full_name() or self.teacher.user.username
        return f"{self.class_obj.name}-{self.section.name} | {self.subject.name} -> {teacher_name}"


class Student(BaseModel):
    """Student profile linked to User."""

    class Gender(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"
        OTHER = "O", "Other"

    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="student_profile",
    )
    classroom = models.ForeignKey(
        ClassRoom,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )
    roll_number = models.CharField(max_length=50)
    admission_number = models.CharField(max_length=50, null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(
        max_length=1,
        choices=Gender.choices,
        blank=True,
        default="",
        db_index=True,
        help_text="Used for reports and demographics (optional).",
    )
    parent_name = models.CharField(max_length=150, blank=True)
    parent_phone = models.CharField(max_length=20, blank=True)

    # Student contact & profile
    # Note: We keep `classroom` and `section` as FKs (existing code relies on them).
    phone = models.CharField(max_length=15, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    profile_image = models.ImageField(upload_to="profiles/", blank=True, null=True)
    extra_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Flexible admission/profile fields (course/branch, documents metadata, medical, billing preferences, etc.).",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["classroom", "section", "roll_number"],
                condition=Q(section__isnull=False),
                name="unique_roll_per_class_section",
            ),
            models.UniqueConstraint(
                fields=["admission_number"],
                condition=Q(admission_number__gt=""),
                name="unique_admission_per_tenant",
            ),
        ]

    def __str__(self) -> str:
        return f"Student: {self.user.get_full_name() or self.user.username}"


class Badge(models.Model):
    """Gamified achievement badge definition (scoped per school tenant schema)."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default="")
    # Store Bootstrap icon class (ASCII) e.g. "bi bi-star-fill"
    icon = models.CharField(max_length=50, default="bi bi-star-fill")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class StudentBadge(models.Model):
    """Tracks which badges were awarded to which student."""

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="badges",
    )
    badge = models.ForeignKey(
        Badge,
        on_delete=models.CASCADE,
        related_name="awarded_students",
    )
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["student", "badge"], name="unique_student_badge"),
        ]
        ordering = ["-awarded_at"]

    def __str__(self) -> str:
        return f"{self.student_id} -> {self.badge_id}"


class StudentDocument(models.Model):
    """Student documents (e.g. birth certificate, transfer certificate)."""
    class DocType(models.TextChoices):
        BIRTH_CERT = "BIRTH_CERT", "Birth Certificate"
        TRANSFER_CERT = "TRANSFER_CERT", "Transfer Certificate"
        PHOTO = "PHOTO", "Photo"
        OTHER = "OTHER", "Other"

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=30, choices=DocType.choices, default=DocType.OTHER)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to="student_docs/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_student_docs",
    )

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"{self.student} - {self.get_doc_type_display()}"


class ExamSession(models.Model):
    """
    Exam session (e.g. Annual Exam 2026) for one class–section.
    Subject-wise schedules are stored as `Exam` rows with `session` set (exam papers).
    """

    name = models.CharField(max_length=100)
    class_name = models.CharField(
        max_length=50,
        db_index=True,
        default="",
        blank=True,
        help_text="Legacy denormalized classroom name (kept for backward compatibility).",
    )
    section = models.CharField(
        max_length=10,
        db_index=True,
        default="",
        blank=True,
        help_text="Legacy denormalized section name (kept for backward compatibility).",
    )
    classroom = models.ForeignKey(
        "ClassRoom",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exam_sessions",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="exam_sessions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Updated when the session or its papers change.",
    )
    display_order = models.PositiveIntegerField(
        default=0,
        db_index=True,
        help_text="Manual card order (0 = use default date-based sort).",
    )
    modified_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exam_sessions_modified",
        help_text="Last school admin who changed this session (name/class/section, etc.).",
    )
    modified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When session metadata was last edited by an admin.",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "exam session"
        verbose_name_plural = "exam sessions"

    def __str__(self) -> str:
        return f"{self.name} ({self.class_name} · {self.section})"


class Exam(models.Model):
    """
    Exam paper: one subject (and date/time) within a class–section.
    When `session` is set, this row is a paper under that exam session; otherwise legacy standalone.
    """
    name = models.CharField(max_length=100)
    # DB column is still `start_date` from initial migrations; ORM field name stays `date`.
    date = models.DateField(db_index=True, db_column="start_date")
    # Legacy `end_date` (NOT NULL in old DB); kept in sync with `date` for single-day exams.
    end_date = models.DateField(
        null=True,
        blank=True,
        db_column="end_date",
        help_text="Legacy column; defaults to the exam date on save.",
    )
    start_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Optional start time for calendar / timetable display.",
    )
    end_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Optional end time for calendar / timetable display.",
    )
    room = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Optional: exam room / hall (e.g., Room 102, Main Hall).",
    )
    details = models.TextField(
        blank=True,
        default="",
        help_text="Optional: instructions / syllabus / notes for this paper.",
    )
    topics = models.TextField(
        blank=True,
        default="",
        help_text="Optional: topics covered (free text).",
    )
    # Legacy NOT NULL FK; denormalized class_name/section remain the source for filters/UI.
    classroom = models.ForeignKey(
        "ClassRoom",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exams",
        db_column="classroom_id",
        help_text="Legacy column; set from class when saving if missing.",
    )
    class_name = models.CharField(
        max_length=50,
        db_index=True,
        default="",
        blank=True,
        help_text="Legacy denormalized classroom name (kept for backward compatibility).",
    )
    section = models.CharField(
        max_length=10,
        db_index=True,
        default="",
        blank=True,
        help_text="Legacy denormalized section name (kept for backward compatibility).",
    )
    subject = models.ForeignKey(
        "Subject",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="exams",
        help_text="When set, this exam is for one subject (single / scheduled exams).",
    )
    total_marks = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=100,
        help_text="Default max marks when teachers enter scores.",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exams_created",
        db_index=True,
    )
    teacher = models.ForeignKey(
        "Teacher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_exams",
        help_text="Optional. When set, this teacher is assigned to the exam.",
    )
    session = models.ForeignKey(
        "ExamSession",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="papers",
        help_text="When set, this row is a subject paper under a multi-subject exam session.",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exams",
    )
    marks_teacher_edit_locked = models.BooleanField(
        default=False,
        help_text="When true, teachers cannot save mark changes until an admin allows re-editing.",
    )

    class Meta:
        verbose_name = "exam paper"
        verbose_name_plural = "exam papers"

    def save(self, *args, **kwargs):
        if self.date is not None and self.end_date is None:
            self.end_date = self.date
        if self.classroom_id is None and self.class_name:
            c = (
                ClassRoom.objects.filter(name__iexact=self.class_name.strip())
                .select_related("academic_year")
                .order_by("-academic_year__start_date", "id")
                .first()
            )
            if c:
                self.classroom = c
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} ({self.class_name} - {self.section})"


class ExamMarkComponent(models.Model):
    """
    Optional breakdown of max marks for one exam paper (one subject).
    When rows exist, Exam.total_marks should match the sum of max_marks (enforced in application code).
    """

    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        related_name="mark_components",
    )
    component_name = models.CharField(max_length=64)
    max_marks = models.PositiveIntegerField()
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["exam", "component_name"],
                name="uniq_exam_mark_component_name",
            ),
        ]
        verbose_name = "exam mark component"
        verbose_name_plural = "exam mark components"

    def __str__(self) -> str:
        return f"{self.component_name} ({self.max_marks})"


class Attendance(models.Model):
    class Status(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        ABSENT = "ABSENT", "Absent"
        LEAVE = "LEAVE", "Leave"

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="attendance_records",
    )
    date = models.DateField()
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_records",
    )
    status = models.CharField(max_length=10, choices=Status.choices)
    marked_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="marked_attendance",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "date")

    def __str__(self) -> str:
        return f"{self.student} - {self.date} - {self.get_status_display()}"


class HolidayCalendar(BaseModel):
    """
    One holiday calendar per academic year. Custom holidays and working-Sunday overrides apply
    only while the calendar is published (built-in Sunday holiday always applies when unpublished).
    """

    academic_year = models.OneToOneField(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name="holiday_calendar",
    )
    name = models.CharField(max_length=120, default="Official holiday calendar")
    is_published = models.BooleanField(default=False, db_index=True)
    use_split_calendars = models.BooleanField(
        default=False,
        help_text="Use separate student vs teacher portal views; events still use “Applies to”.",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    unpublished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-academic_year__start_date", "id"]

    def __str__(self) -> str:
        pub = " (published)" if self.is_published else ""
        return f"{self.academic_year.name}: {self.name}{pub}"


class HolidayEvent(BaseModel):
    """Named holiday or closure on a single calendar day (end_date mirrors start_date in storage)."""

    class HolidayType(models.TextChoices):
        NATIONAL = "NATIONAL", "National Holiday"
        FESTIVAL = "FESTIVAL", "Festival Holiday"
        SCHOOL = "SCHOOL", "School Holiday"
        EMERGENCY = "EMERGENCY", "Emergency Closure"
        EXAM_LEAVE = "EXAM_LEAVE", "Exam Leave"
        VACATION = "VACATION", "Vacation"
        SPECIAL = "SPECIAL", "Special Holiday"

    class AppliesTo(models.TextChoices):
        STUDENTS = "STUDENTS", "Students only"
        TEACHERS = "TEACHERS", "Teachers only"
        BOTH = "BOTH", "Students and teachers"

    calendar = models.ForeignKey(
        HolidayCalendar,
        on_delete=models.CASCADE,
        related_name="events",
    )
    name = models.CharField(max_length=200)
    holiday_type = models.CharField(max_length=20, choices=HolidayType.choices, db_index=True)
    start_date = models.DateField(db_index=True, verbose_name="Holiday date")
    end_date = models.DateField(db_index=True, help_text="Must match holiday date; kept for schema compatibility.")
    applies_to = models.CharField(
        max_length=16,
        choices=AppliesTo.choices,
        default=AppliesTo.BOTH,
        db_index=True,
    )
    description = models.TextField(blank=True)
    recurring_yearly = models.BooleanField(
        default=False,
        help_text="If set, this holiday repeats on the same month/day each year within the academic year.",
    )

    class Meta:
        ordering = ["start_date", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_holiday_type_display()})"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("End date must be on or after start date.")
        if self.start_date and self.end_date and self.start_date != self.end_date:
            raise ValidationError("Holidays must be a single day; end date must match the holiday date.")

    def save(self, *args, **kwargs):
        if self.start_date:
            self.end_date = self.start_date
        elif self.end_date:
            self.start_date = self.end_date
        super().save(*args, **kwargs)


class WorkingSundayOverride(BaseModel):
    """Mark a specific Sunday as a working day for the chosen audience (overrides default Sunday holiday)."""

    class AppliesTo(models.TextChoices):
        STUDENTS = "STUDENTS", "Students only"
        TEACHERS = "TEACHERS", "Teachers only"
        BOTH = "BOTH", "Students and teachers"

    calendar = models.ForeignKey(
        HolidayCalendar,
        on_delete=models.CASCADE,
        related_name="working_sunday_overrides",
    )
    work_date = models.DateField(db_index=True)
    applies_to = models.CharField(max_length=16, choices=AppliesTo.choices, default=AppliesTo.BOTH)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["work_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["calendar", "work_date", "applies_to"],
                name="school_working_sunday_unique_cal_date_audience",
            ),
        ]

    def __str__(self) -> str:
        return f"Working {self.work_date} ({self.get_applies_to_display()})"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.work_date and self.work_date.weekday() != 6:
            raise ValidationError("Working day override is intended for Sundays only.")


class Marks(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="marks",
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name="marks",
    )
    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="marks",
    )
    exam_name = models.CharField(max_length=100, blank=True)
    exam_date = models.DateField(null=True, blank=True)
    marks_obtained = models.PositiveIntegerField()
    total_marks = models.PositiveIntegerField()
    # Optional per-paper breakdown (e.g. {"Theory": 55, "Practical": 18}).
    # When present, marks_obtained should equal sum(values) and total_marks should equal Exam.total_marks.
    component_marks = models.JSONField(default=dict, blank=True)
    entered_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="entered_marks",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "subject", "exam"],
                name="unique_student_subject_exam",
                condition=Q(exam__isnull=False),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} - {self.subject} - {self.exam or self.exam_name}"


class Grade(models.Model):
    """Grade definition for report cards (e.g. A: 90-100, B: 80-89)."""
    name = models.CharField(max_length=10)
    min_percentage = models.PositiveIntegerField()
    max_percentage = models.PositiveIntegerField()
    description = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-min_percentage"]

    def __str__(self) -> str:
        return f"{self.name} ({self.min_percentage}-{self.max_percentage}%)"


class Homework(BaseModel):
    """Homework assigned to class(es) and section(s). Legacy subject/teacher kept for backward compat."""

    class HomeworkType(models.TextChoices):
        CLASSWORK = "CLASSWORK", "Classwork"
        HOMEWORK = "HOMEWORK", "Homework"
        PROJECT = "PROJECT", "Project"
        ASSIGNMENT = "ASSIGNMENT", "Assignment"
        REVISION = "REVISION", "Revision work"
        LAB = "LAB", "Lab work"
        READING = "READING", "Reading task"

    class SubmissionType(models.TextChoices):
        NOTEBOOK = "NOTEBOOK", "Notebook submission"
        ONLINE = "ONLINE", "Online upload"
        BOTH = "BOTH", "Both"
        ORAL = "ORAL", "Oral / practical"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        NORMAL = "NORMAL", "Normal"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        CLOSED = "CLOSED", "Closed"
        ARCHIVED = "ARCHIVED", "Archived"

    title = models.CharField(max_length=200)
    description = models.TextField()
    due_date = models.DateField(db_index=True)
    assigned_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Date the work was given (defaults to today on create if left blank).",
    )
    homework_type = models.CharField(
        max_length=50,
        choices=HomeworkType.choices,
        default=HomeworkType.HOMEWORK,
        db_index=True,
    )
    max_marks = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Optional cap for internal grading.",
    )
    submission_type = models.CharField(
        max_length=50,
        choices=SubmissionType.choices,
        default=SubmissionType.NOTEBOOK,
    )
    allow_late_submission = models.BooleanField(default=False)
    late_submission_until = models.DateTimeField(null=True, blank=True)
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.NORMAL,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PUBLISHED,
        db_index=True,
        help_text="Draft is hidden from students; published and closed are visible; archived is hidden.",
    )
    academic_year = models.ForeignKey(
        "AcademicYear",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="homeworks",
    )
    estimated_duration_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Rough effort estimate for students and parents.",
    )
    instructions = models.TextField(
        blank=True,
        default="",
        help_text="How to submit, materials, format — separate from task description.",
    )
    submission_required = models.BooleanField(
        default=True,
        help_text="If off, students see the task but no file upload is expected.",
    )
    assigned_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="assigned_homework",
        null=True,
        blank=True,
    )
    classes = models.ManyToManyField(
        ClassRoom,
        related_name="homeworks",
        blank=True,
    )
    sections = models.ManyToManyField(
        Section,
        related_name="homeworks",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # Legacy fields - nullable for backward compat
    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="homeworks",
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="homeworks",
    )
    attachment = models.FileField(
        upload_to="homework_attachments/%Y/%m/",
        null=True,
        blank=True,
        help_text="Optional worksheet or reference file for students.",
    )

    def save(self, *args, **kwargs):
        if self.pk is None and self.assigned_date is None:
            from django.utils import timezone as dj_tz

            self.assigned_date = dj_tz.now().date()
        super().save(*args, **kwargs)

    def is_past_submission_deadline(self, when=None):
        """True when the student can no longer submit (after due date, respecting late rules)."""
        from django.utils import timezone as dj_tz

        when = when or dj_tz.now()
        today = when.date()
        if self.due_date >= today:
            return False
        if self.allow_late_submission:
            if self.late_submission_until is None:
                return False
            return when > self.late_submission_until
        return True

    def is_visible_to_students(self) -> bool:
        return self.status in (self.Status.PUBLISHED, self.Status.CLOSED)

    def allows_new_submission(self) -> bool:
        return self.status == self.Status.PUBLISHED

    @property
    def is_due_overdue(self) -> bool:
        """Template-friendly: past due / late window (for badges)."""
        return self.is_past_submission_deadline()

    class Meta:
        ordering = ["-due_date", "-created_at"]

    def __str__(self) -> str:
        return f"Homework: {self.title}"


class HomeworkSubmission(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"

    homework = models.ForeignKey(
        Homework,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="homework_submissions",
    )
    submission_file = models.FileField(upload_to="homework_submissions/%Y/%m/", null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["homework", "student"],
                name="unique_homework_submission_per_student",
            ),
        ]
        ordering = ["-submitted_at"]

    def __str__(self) -> str:
        return f"{self.student} - {self.homework} - {self.status}"


class FeeType(BaseModel):
    """Fee category: Tuition, Transport, etc."""
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class FeeStructure(BaseModel):
    """Fee amount per class/term — drives class-wise billing for all students in that class."""

    class Frequency(models.TextChoices):
        ONE_TIME = "ONE_TIME", "One Time"
        MONTHLY = "MONTHLY", "Monthly"
        QUARTERLY = "QUARTERLY", "Quarterly"
        HALF_YEARLY = "HALF_YEARLY", "Half Yearly"
        YEARLY = "YEARLY", "Yearly"
        TERM_WISE = "TERM_WISE", "Term Wise"
        SEMESTER = "SEMESTER", "Semester"

    fee_type = models.ForeignKey(FeeType, on_delete=models.CASCADE, related_name="structures")
    line_name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Optional label on receipts (defaults to fee type name if empty).",
    )
    classroom = models.ForeignKey(
        ClassRoom,
        on_delete=models.CASCADE,
        related_name="fee_structures",
        null=True,
        blank=True,
    )
    section = models.ForeignKey(
        "Section",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="fee_structures",
        help_text="Optional: limit this fee head to one section. Leave empty for all sections in the class.",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name="fee_structures",
        null=True,
        blank=True,
    )
    frequency = models.CharField(
        max_length=20,
        choices=Frequency.choices,
        default=Frequency.MONTHLY,
        help_text="Billing cycle for display and planning.",
    )
    due_day_of_month = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Typical due day of month (1–28) for this fee.",
    )
    first_due_date = models.DateField(
        null=True,
        blank=True,
        help_text="When set, used as the due date for auto-created student fee lines (otherwise computed from due day).",
    )
    installments_enabled = models.BooleanField(
        default=False,
        help_text="Marks this head as installment-capable (schedules are configured separately).",
    )
    late_fine_rule = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Reference label for late-fine policy (configure rules under Late Fine Rules).",
    )
    discount_allowed = models.BooleanField(
        default=True,
        help_text="If disabled, staff are discouraged from applying concessions on this head.",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    batch_key = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Groups structure rows created in one wizard save (multi-section / multi-fee batch).",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["fee_type", "classroom", "academic_year"],
                condition=models.Q(section__isnull=True),
                name="school_data_feestructure_unique_class_wide",
            ),
            models.UniqueConstraint(
                fields=["fee_type", "classroom", "academic_year", "section"],
                condition=models.Q(section__isnull=False),
                name="school_data_feestructure_unique_section",
            ),
        ]
        ordering = ["fee_type", "classroom__grade_order", "classroom__name"]

    def __str__(self) -> str:
        label = (self.line_name or "").strip() or (self.fee_type.name if self.fee_type_id else "Fee")
        return f"{label} - {self.classroom or 'All'} - {self.amount}"


class Fee(BaseModel):
    """Fee due for a student."""

    class ConcessionKind(models.TextChoices):
        NONE = "NONE", "Fixed / percentage only"
        MANUAL = "MANUAL", "Manual concession"
        SCHOLARSHIP = "SCHOLARSHIP", "Scholarship"
        SIBLING = "SIBLING", "Sibling discount"
        STAFF_CHILD = "STAFF_CHILD", "Staff child"

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="fees")
    fee_structure = models.ForeignKey(
        FeeStructure,
        on_delete=models.CASCADE,
        related_name="fees",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_date = models.DateField()
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fees",
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ("PENDING", "Pending"),
            ("PARTIAL", "Partial"),
            ("PAID", "Paid"),
        ],
        default="PENDING",
    )
    concession_fixed = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Flat discount off this fee line (after percentage, if any).",
    )
    concession_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Percentage discount on the original fee amount (0–100).",
    )
    concession_kind = models.CharField(
        max_length=20,
        choices=ConcessionKind.choices,
        default=ConcessionKind.NONE,
    )
    concession_note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-due_date"]

    def __str__(self) -> str:
        return f"{self.student} - {self.fee_structure} - {self.amount}"

    @property
    def effective_due_amount(self) -> Decimal:
        """Net amount collectible after percentage + fixed concession."""
        base = self.amount or Decimal("0")
        pct = self.concession_percent or Decimal("0")
        fixed = self.concession_fixed or Decimal("0")
        pct_amt = (base * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        discount = pct_amt + fixed
        if discount <= 0:
            return base
        if discount >= base:
            return Decimal("0")
        return (base - discount).quantize(Decimal("0.01"))

    @property
    def total_concession_amount(self) -> Decimal:
        return (self.amount or Decimal("0")) - self.effective_due_amount


class StudentEnrollment(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        PROMOTED = "PROMOTED", "Promoted"
        DEMOTED = "DEMOTED", "Demoted"
        TRANSFERRED = "TRANSFERRED", "Transferred"
        DETAINED = "DETAINED", "Detained"

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="enrollments")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="enrollments")
    classroom = models.ForeignKey(
        ClassRoom,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    is_current = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "academic_year"],
                name="unique_student_enrollment_per_year",
            ),
            models.UniqueConstraint(
                fields=["student"],
                condition=Q(is_current=True),
                name="unique_current_enrollment_per_student",
            ),
        ]
        ordering = ["-created_at"]


class StudentPromotion(models.Model):
    class Action(models.TextChoices):
        PROMOTE = "PROMOTE", "Promote"
        DEMOTE = "DEMOTE", "Demote"
        TRANSFER = "TRANSFER", "Transfer"
        DETAIN = "DETAIN", "Detain"

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="promotions")
    from_class = models.ForeignKey(
        ClassRoom,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="promotions_from",
    )
    to_class = models.ForeignKey(
        ClassRoom,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="promotions_to",
    )
    from_section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="promotions_from_section",
    )
    to_section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="promotions_to_section",
    )
    from_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="promotions_from_year")
    to_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="promotions_to_year")
    action = models.CharField(max_length=20, choices=Action.choices)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_promotions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "from_year", "to_year"],
                name="unique_student_promotion_year_pair",
            )
        ]
        ordering = ["-created_at"]


class PaymentBatch(models.Model):
    """One logical payment (single receipt) allocated across multiple fee lines."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="payment_batches")
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_batches",
    )
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    payment_method = models.CharField(max_length=50, default="Cash")
    receipt_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="School receipt / voucher number (optional).",
    )
    transaction_reference = models.CharField(
        max_length=120,
        blank=True,
        help_text="UPI ref., bank ref., or online payment id (optional).",
    )
    notes = models.TextField(blank=True)
    received_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_batches_received",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    receipt_code = models.CharField(
        max_length=32,
        blank=True,
        db_index=True,
        help_text="Auto-generated receipt no., e.g. RCPT-2026-000042.",
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"Batch ₹{self.total_amount} — {self.student_id}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not (self.receipt_code or "").strip():
            y = self.payment_date.year
            code = f"RCPT-{y}-{self.pk:06d}"
            type(self).objects.filter(pk=self.pk).update(receipt_code=code)
            self.receipt_code = code


class PaymentBatchTender(models.Model):
    """Split of one receipt across payment modes (e.g. part cash, part UPI)."""

    batch = models.ForeignKey(
        PaymentBatch,
        on_delete=models.CASCADE,
        related_name="tenders",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=50, default="Cash")
    transaction_reference = models.CharField(
        max_length=120,
        blank=True,
        help_text="Optional ref for this tender (UPI / cheque no. / etc.).",
    )

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.payment_method} {self.amount}"


class Payment(BaseModel):
    """Payment record against a fee."""
    fee = models.ForeignKey(Fee, on_delete=models.CASCADE, related_name="payments")
    batch = models.ForeignKey(
        PaymentBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="line_payments",
        help_text="When set, this row is part of a multi-line payment.",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    payment_method = models.CharField(max_length=50, default="Cash")
    receipt_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="School receipt / voucher number (optional).",
    )
    transaction_reference = models.CharField(
        max_length=120,
        blank=True,
        help_text="UPI ref., bank ref., or online payment id (optional).",
    )
    notes = models.TextField(blank=True)
    received_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_payments",
    )

    class Meta:
        ordering = ["-payment_date"]

    def __str__(self) -> str:
        return f"Payment {self.amount} for {self.fee}"


class PaymentReceipt(models.Model):
    """Stores receipt metadata for PDF generation and tracking."""
    payment = models.OneToOneField(
        Payment,
        on_delete=models.CASCADE,
        related_name="receipt",
    )
    receipt_number = models.CharField(max_length=50, db_index=True)
    generated_at = models.DateTimeField(auto_now_add=True)
    generated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_receipts",
    )

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self) -> str:
        return f"Receipt {self.receipt_number}"


# ---------- Parent & Parent Portal ----------

class Parent(BaseModel):
    """Parent linked to students."""
    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="parent_profile",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20)
    relation = models.CharField(max_length=50, default="Parent")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def students(self):
        return Student.objects.filter(guardians__parent=self)


class StudentParent(BaseModel):
    """Links Student to Parent (many-to-many)."""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="guardians")
    parent = models.ForeignKey(Parent, on_delete=models.CASCADE, related_name="children")

    class Meta:
        unique_together = ("student", "parent")

    def __str__(self) -> str:
        return f"{self.parent} - {self.student}"


class Announcement(models.Model):
    """Announcements visible to parents and students."""
    title = models.CharField(max_length=200)
    content = models.TextField()
    published_at = models.DateTimeField(auto_now_add=True)
    published_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_announcements",
    )
    is_pinned = models.BooleanField(default=False)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self) -> str:
        return self.title


# ---------- Staff Attendance ----------

class StaffAttendance(models.Model):
    """Attendance records for teachers/staff. One record per staff per date."""
    class Status(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        ABSENT = "ABSENT", "Absent"
        LEAVE = "LEAVE", "Leave"
        HALF_DAY = "HALF_DAY", "Half Day"
        HOLIDAY = "HOLIDAY", "Holiday"
        OTHER = "OTHER", "Other"

    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="attendance_records",
    )
    date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices)
    remarks = models.CharField(max_length=200, blank=True)
    marked_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="marked_staff_attendance",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("teacher", "date")
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"{self.teacher} - {self.date} - {self.get_status_display()}"


# ---------- Support ----------

class SupportTicket(BaseModel):
    """Support ticket."""
    subject = models.CharField(max_length=200)
    message = models.TextField()
    priority = models.CharField(
        max_length=20,
        choices=[
            ("LOW", "Low"),
            ("MEDIUM", "Medium"),
            ("HIGH", "High"),
            ("PRIORITY", "Priority"),
        ],
        default="MEDIUM",
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ("OPEN", "Open"),
            ("IN_PROGRESS", "In Progress"),
            ("RESOLVED", "Resolved"),
        ],
        default="OPEN",
    )
    submitted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets",
    )

    class Meta:
        ordering = ["-created_on"]

    def __str__(self) -> str:
        return f"Ticket #{self.pk} - {self.subject}"


class SupportMessage(models.Model):
    """Reply/thread messages for support tickets."""
    ticket = models.ForeignKey(
        SupportTicket,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    message = models.TextField()
    is_staff_reply = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_messages",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"#{self.ticket_id} - {self.created_at}"


# ---------- Inventory & Invoicing ----------

class InventoryItem(BaseModel):
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=50, blank=True)
    unit = models.CharField(max_length=20, default="pcs")
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    min_stock = models.DecimalField(max_digits=12, decimal_places=2, default=0, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0, null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.sku or 'N/A'})"


class InventoryTransaction(models.Model):
    """Stock in/out ledger for inventory."""
    class TransactionType(models.TextChoices):
        IN = "IN", "Stock In"
        OUT = "OUT", "Stock Out"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    inventory_item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_transactions",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.transaction_type} {self.quantity} - {self.inventory_item}"


class Purchase(BaseModel):
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="purchases")
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    purchase_date = models.DateField()
    supplier = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-purchase_date"]

    def __str__(self) -> str:
        return f"Purchase {self.quantity} x {self.inventory_item}"


class Invoice(BaseModel):
    invoice_number = models.CharField(max_length=50, db_index=True)
    customer_name = models.CharField(max_length=200)
    customer_address = models.TextField(blank=True)
    issue_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20,
        choices=[("DRAFT", "Draft"), ("ISSUED", "Issued"), ("PAID", "Paid")],
        default="DRAFT",
    )

    class Meta:
        ordering = ["-issue_date"]

    def __str__(self) -> str:
        return f"Invoice {self.invoice_number}"


class InvoiceItem(BaseModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=300)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self) -> str:
        return f"{self.invoice.invoice_number} - {self.description}"


# ---------- Online Admissions ----------

class OnlineAdmission(BaseModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    date_of_birth = models.DateField()
    parent_name = models.CharField(max_length=150)
    parent_phone = models.CharField(max_length=20)
    address = models.TextField(blank=True)
    applied_class = models.ForeignKey(
        ClassRoom,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admission_applications",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    application_number = models.CharField(max_length=50, blank=True)
    remarks = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_admissions",
    )

    class Meta:
        ordering = ["-created_on"]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name} - {self.status}"


class ApplicationDocument(models.Model):
    """Documents attached to online admission applications."""
    application = models.ForeignKey(
        OnlineAdmission,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to="admission_docs/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"{self.application.application_number} - {self.title}"


# ---------- Admissions Management (Admin module) ----------


class Admission(BaseModel):
    """
    Tenant-scoped admissions application tracked by school admin staff.
    This is separate from OnlineAdmission (public web form).
    """

    class Status(models.TextChoices):
        NEW = "NEW", "New"
        UNDER_REVIEW = "UNDER_REVIEW", "Under review"
        DOCUMENT_PENDING = "DOCUMENT_PENDING", "Document pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        JOINED = "JOINED", "Joined"

    application_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        help_text="Auto generated like ADM-2026-0001",
    )
    admission_number = models.CharField(
        max_length=40,
        unique=True,
        db_index=True,
        editable=False,
        blank=True,
        default="",
        help_text="Auto generated like 26chs_2536486vc",
    )
    admission_date = models.DateField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.NEW, db_index=True)

    # Student details
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150, blank=True, default="")
    gender = models.CharField(max_length=60, blank=True, default="")
    date_of_birth = models.DateField(null=True, blank=True)
    blood_group = models.CharField(max_length=20, blank=True, default="")
    aadhaar_or_student_id = models.CharField(max_length=32, blank=True, default="", db_index=True)
    passport_photo = models.ImageField(upload_to="admissions/photos/%Y/%m/", blank=True, null=True)

    # Parent details (quick)
    father_name = models.CharField(max_length=150, blank=True, default="")
    mother_name = models.CharField(max_length=150, blank=True, default="")
    mobile_number = models.CharField(max_length=20, blank=True, default="", db_index=True)
    alternate_mobile = models.CharField(max_length=20, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    occupation = models.CharField(max_length=120, blank=True, default="")
    annual_income = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Address
    house_no = models.CharField(max_length=80, blank=True, default="")
    street = models.CharField(max_length=180, blank=True, default="")
    city = models.CharField(max_length=120, blank=True, default="")
    state = models.CharField(max_length=120, blank=True, default="")
    pincode = models.CharField(max_length=12, blank=True, default="")

    # Academic
    applying_for_class = models.ForeignKey(
        ClassRoom,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admissions_applications",
    )
    previous_school_name = models.CharField(max_length=200, blank=True, default="")
    previous_marks_percent = models.CharField(max_length=50, blank=True, default="")

    # Transport
    require_bus = models.BooleanField(default=False)
    pickup_point = models.CharField(max_length=160, blank=True, default="")

    notes = models.TextField(blank=True, default="")

    # Integration links (after approval)
    created_student = models.ForeignKey(
        "school_data.Student",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_from_admissions",
    )
    approved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admissions_approved",
    )
    rejected_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admissions_rejected",
    )

    class Meta:
        ordering = ["-created_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["aadhaar_or_student_id"],
                condition=Q(aadhaar_or_student_id__gt=""),
                name="uniq_admission_aadhaar_per_tenant",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.application_id} - {self.first_name} {self.last_name}".strip()

    @staticmethod
    def next_application_id(today=None) -> str:
        """
        Generate a tenant-unique application id like ADM-2026-0001.
        """
        from django.utils import timezone

        d = today or timezone.localdate()
        prefix = f"ADM-{d.year}-"
        last = (
            Admission.objects.filter(application_id__startswith=prefix)
            .order_by("-application_id")
            .values_list("application_id", flat=True)
            .first()
        )
        n = 1
        if last and isinstance(last, str) and last.startswith(prefix):
            try:
                n = int(last.replace(prefix, "").strip() or "0") + 1
            except Exception:
                n = 1
        return f"{prefix}{n:04d}"

    def save(self, *args, **kwargs):
        if not self.application_id:
            self.application_id = self.next_application_id()
        if not self.admission_number:
            from django.conf import settings
            from apps.core.admissions_utils import generate_admission_number

            school_code = getattr(settings, "ADMISSIONS_SCHOOL_SHORT_CODE", "chs")
            # Retry on duplicates
            for _ in range(25):
                candidate = generate_admission_number(
                    self.first_name or "",
                    self.last_name or "",
                    "",
                    school_code,
                )
                if not Admission.objects.filter(admission_number=candidate).exists():
                    self.admission_number = candidate
                    break
        super().save(*args, **kwargs)


class AdmissionDocument(models.Model):
    class DocType(models.TextChoices):
        TRANSFER_CERT = "TRANSFER_CERT", "Transfer certificate"
        BIRTH_CERT = "BIRTH_CERT", "Birth certificate"
        OTHER = "OTHER", "Other"

    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=40, choices=DocType.choices, default=DocType.OTHER, db_index=True)
    title = models.CharField(max_length=200, blank=True, default="")
    file = models.FileField(upload_to="admissions/docs/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"{self.admission.application_id} - {self.doc_type}"


class AdmissionStatusHistory(BaseModel):
    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=32, blank=True, default="")
    to_status = models.CharField(max_length=32, db_index=True)
    note = models.CharField(max_length=240, blank=True, default="")

    class Meta:
        ordering = ["-created_on", "-id"]

    def __str__(self) -> str:
        return f"{self.admission.application_id}: {self.from_status} -> {self.to_status}"


# ---------- Library ----------

class Book(BaseModel):
    title = models.CharField(max_length=300)
    author = models.CharField(max_length=200, blank=True)
    isbn = models.CharField(max_length=20, blank=True)
    category = models.CharField(max_length=100, blank=True)
    total_copies = models.PositiveIntegerField(default=1)
    available_copies = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["title"]

    def __str__(self) -> str:
        return f"{self.title} ({self.author or 'Unknown'})"


class BookIssue(BaseModel):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="issues")
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="book_issues")
    issue_date = models.DateField()
    due_date = models.DateField()
    return_date = models.DateField(null=True, blank=True)
    late_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["-issue_date"]

    def __str__(self) -> str:
        return f"{self.book.title} -> {self.student}"


# ---------- Hostel ----------

class Hostel(BaseModel):
    name = models.CharField(max_length=100)

    def __str__(self) -> str:
        return self.name


class HostelRoom(BaseModel):
    hostel = models.ForeignKey(Hostel, on_delete=models.CASCADE, related_name="rooms")
    room_number = models.CharField(max_length=20)
    capacity = models.PositiveIntegerField(default=1)
    room_type = models.CharField(max_length=50, blank=True)

    class Meta:
        unique_together = ("hostel", "room_number")

    def __str__(self) -> str:
        return f"{self.hostel.name} - {self.room_number}"


class HostelAllocation(BaseModel):
    room = models.ForeignKey(HostelRoom, on_delete=models.CASCADE, related_name="allocations")
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="hostel_allocations")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self) -> str:
        return f"{self.student} in {self.room}"


class HostelFee(BaseModel):
    hostel = models.ForeignKey(Hostel, on_delete=models.CASCADE, related_name="fees")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_date = models.DateField()
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="hostel_fees",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=[("PENDING", "Pending"), ("PAID", "Paid")],
        default="PENDING",
    )


# ---------- Transport ----------

class Route(BaseModel):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    def __str__(self) -> str:
        return self.name


class Vehicle(BaseModel):
    registration_number = models.CharField(max_length=30)
    vehicle_type = models.CharField(max_length=50, blank=True)
    capacity = models.PositiveIntegerField(default=30)
    route = models.ForeignKey(
        Route,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vehicles",
    )

    def __str__(self) -> str:
        return f"{self.registration_number} ({self.route or 'Unassigned'})"


class Driver(BaseModel):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True)
    license_number = models.CharField(max_length=50, blank=True)
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="drivers",
    )

    def __str__(self) -> str:
        return self.name


class StudentRouteAssignment(BaseModel):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="route_assignments")
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="assignments")
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments",
    )
    pickup_point = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = ("student", "route")

    def __str__(self) -> str:
        return f"{self.student} -> {self.route}"


class InternalChatThread(models.Model):
    """
    One thread per pair of users (tenant-scoped). user_low.id is always < user_high.id.
    School admin ↔ teacher, or teacher ↔ student (same school).
    """

    user_low = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="internal_chat_threads_low",
    )
    user_high = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="internal_chat_threads_high",
    )
    last_message_at = models.DateTimeField(db_index=True)
    user_low_last_read_at = models.DateTimeField(null=True, blank=True)
    user_high_last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("user_low", "user_high")]
        ordering = ["-last_message_at"]

    def __str__(self) -> str:
        return f"Chat {self.user_low_id}–{self.user_high_id}"


class InternalChatMessage(models.Model):
    thread = models.ForeignKey(
        InternalChatThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="internal_chat_messages_sent",
    )
    body = models.TextField(max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Msg {self.pk} from {self.sender_id}"
