from django.db import models
from django.db.models import Q


class School(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)
    address = models.TextField(blank=True)
    logo = models.ImageField(upload_to="school_logos/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class AcademicYear(models.Model):
    """Academic year (e.g. 2025-2026). Only one can be active per school."""
    name = models.CharField(max_length=50, db_index=True)  # e.g. "2025-2026"
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=False, db_index=True)
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="academic_years",
    )

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["school"],
                condition=Q(is_active=True),
                name="unique_active_academic_year_per_school",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"
    
    def save(self, *args, **kwargs):
        if self.is_active:
            AcademicYear.objects.filter(school=self.school, is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)


class ClassRoom(models.Model):
    """Grade level (e.g. Grade 1, Grade 10). Sections are defined via Section model."""
    name = models.CharField(max_length=50, db_index=True)  # e.g. "Grade 1", "Grade 10"
    section = models.CharField(max_length=10, blank=True)  # Legacy: backward compat
    description = models.TextField(blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    academic_year = models.ForeignKey(
        "AcademicYear",
        on_delete=models.CASCADE,
        related_name="classrooms",
        null=True,
        blank=True,
    )
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="classrooms",
    )

    class Meta:
        unique_together = ("name", "section", "school")
        constraints = [
            models.UniqueConstraint(
                fields=["name", "academic_year", "school"],
                condition=Q(academic_year__isnull=False),
                name="unique_classroom_name_per_academic_year",
            ),
        ]
        ordering = ["academic_year", "name"]

    def __str__(self) -> str:
        if self.section:
            return f"{self.name}-{self.section} ({self.school.code})"
        return f"{self.name} ({self.school.code})"


class Section(models.Model):
    """Custom section names (Alpha, Beta, Gamma, etc.) within a classroom."""
    name = models.CharField(max_length=50)
    classroom = models.ForeignKey(
        ClassRoom,
        on_delete=models.CASCADE,
        related_name="sections",
    )
    class_teacher = models.ForeignKey(
        "Teacher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="class_teacher_sections",
    )
    capacity = models.PositiveIntegerField(null=True, blank=True)
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="sections",
    )

    class Meta:
        unique_together = ("classroom", "name")
        ordering = ["classroom", "name"]

    def __str__(self) -> str:
        return f"{self.classroom}-{self.name}"
    
    def clean(self):
        from django.core.exceptions import ValidationError
        if self.capacity is not None and self.classroom_id and self.classroom.capacity is not None:
            if self.capacity > self.classroom.capacity:
                raise ValidationError({"capacity": "Section capacity cannot exceed classroom capacity (%s)." % self.classroom.capacity})


class Student(models.Model):
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
    roll_number = models.CharField(max_length=50)
    admission_number = models.CharField(max_length=50, null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    parent_name = models.CharField(max_length=150, blank=True)
    parent_phone = models.CharField(max_length=20, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["section", "roll_number"],
                condition=Q(section__isnull=False),
                name="unique_roll_per_section",
            ),
        ]

    def __str__(self) -> str:
        return f"Student: {self.user.get_full_name() or self.user.username}"


class Exam(models.Model):
    """Exam for a classroom (e.g., Mid Term, Final Exam)."""
    name = models.CharField(max_length=100)
    classroom = models.ForeignKey(
        ClassRoom,
        on_delete=models.CASCADE,
        related_name="exams",
    )
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="exams",
    )
    start_date = models.DateField()
    end_date = models.DateField()

    def __str__(self) -> str:
        return f"{self.name} ({self.classroom})"


class Subject(models.Model):
    name = models.CharField(max_length=100, db_index=True)
    code = models.CharField(max_length=50, blank=True, db_index=True)  # e.g. MATH101, PHY101
    classroom = models.ForeignKey(
        ClassRoom,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="subjects",
    )
    teacher = models.ForeignKey(
        "Teacher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_subjects",
    )
    academic_year = models.ForeignKey(
        "AcademicYear",
        on_delete=models.CASCADE,
        related_name="subjects",
        null=True,
        blank=True,
    )
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="subjects",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["code", "classroom", "academic_year"],
                condition=Q(code__gt="", academic_year__isnull=False),
                name="unique_subject_code_per_class_year",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"


class Teacher(models.Model):
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
    employee_id = models.CharField(max_length=50, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    qualification = models.CharField(max_length=200, blank=True)
    experience = models.CharField(max_length=100, blank=True, help_text="e.g. 5 years")

    # Legacy: for backward compat during migration (use subjects.first() if needed)
    subject = models.ForeignKey(
        Subject,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teachers_legacy",
    )

    def __str__(self) -> str:
        return f"Teacher: {self.user.get_full_name() or self.user.username}"


class Attendance(models.Model):
    class Status(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        ABSENT = "ABSENT", "Absent"

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="attendance_records",
    )
    date = models.DateField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
    )
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


class Homework(models.Model):
    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name="homeworks",
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="homeworks",
    )
    title = models.CharField(max_length=200)
    description = models.TextField()
    due_date = models.DateField()

    def __str__(self) -> str:
        return f"Homework: {self.title} ({self.subject})"
