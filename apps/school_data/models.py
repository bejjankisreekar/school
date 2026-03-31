"""
Tenant-schema models for School ERP. Each school has its own PostgreSQL schema.
These models have no school FK - the schema defines the tenant.
User FK to accounts.User is kept (User lives in public schema).
"""
from django.db import models, transaction
from django.db.models import Q

# BaseModel for audit - uses accounts.User (public schema)
from apps.core.models import BaseModel


class AcademicYear(BaseModel):
    """Academic year (e.g. 2025-2026). When saving as active, other years are cleared first (app-level)."""
    name = models.CharField(max_length=50, db_index=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=False, db_index=True)

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
        ordering = ["academic_year", "name"]

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

    class Meta:
        ordering = ["name"]

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


class Homework(models.Model):
    """Homework assigned to class(es) and section(s). Legacy subject/teacher kept for backward compat."""
    title = models.CharField(max_length=200)
    description = models.TextField()
    due_date = models.DateField(db_index=True)
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

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class FeeStructure(BaseModel):
    """Fee amount per class/term."""
    fee_type = models.ForeignKey(FeeType, on_delete=models.CASCADE, related_name="structures")
    classroom = models.ForeignKey(
        ClassRoom,
        on_delete=models.CASCADE,
        related_name="fee_structures",
        null=True,
        blank=True,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name="fee_structures",
        null=True,
        blank=True,
    )

    class Meta:
        unique_together = [("fee_type", "classroom", "academic_year")]
        ordering = ["fee_type", "classroom"]

    def __str__(self) -> str:
        return f"{self.fee_type.name} - {self.classroom or 'All'} - {self.amount}"


class Fee(BaseModel):
    """Fee due for a student."""
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

    class Meta:
        ordering = ["-due_date"]

    def __str__(self) -> str:
        return f"{self.student} - {self.fee_structure} - {self.amount}"


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


class Payment(BaseModel):
    """Payment record against a fee."""
    fee = models.ForeignKey(Fee, on_delete=models.CASCADE, related_name="payments")
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
