from django.db import models


class School(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)
    address = models.TextField(blank=True)
    logo = models.ImageField(upload_to="school_logos/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class ClassRoom(models.Model):
    """Class-Section (e.g., 10-A, 10-B)."""
    name = models.CharField(max_length=20)  # e.g. "10"
    section = models.CharField(max_length=10)  # e.g. "A", "B"
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="classrooms",
    )

    class Meta:
        unique_together = ("name", "section", "school")

    def __str__(self) -> str:
        return f"{self.name}-{self.section} ({self.school.code})"


class Student(models.Model):
    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="student_profile",
    )
    grade = models.CharField(max_length=20)
    section = models.CharField(max_length=20, blank=True)
    roll_number = models.CharField(max_length=50)
    classroom = models.ForeignKey(
        ClassRoom,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )

    def __str__(self) -> str:
        return f"Student: {self.user.get_full_name() or self.user.username}"


class Subject(models.Model):
    name = models.CharField(max_length=100)
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="subjects",
    )

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"


class Teacher(models.Model):
    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="teacher_profile",
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teachers",
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
    exam_name = models.CharField(max_length=100)
    exam_date = models.DateField(null=True, blank=True)
    marks_obtained = models.PositiveIntegerField()
    total_marks = models.PositiveIntegerField()

    def __str__(self) -> str:
        return f"{self.student} - {self.subject} - {self.exam_name}"


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
