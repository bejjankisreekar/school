from django.conf import settings
from django.db import models


class NotificationChannel(models.TextChoices):
    SMS = "SMS", "SMS"
    EMAIL = "EMAIL", "Email"
    BOTH = "BOTH", "SMS + Email"


class NotificationTargetType(models.TextChoices):
    ALL_STUDENTS = "ALL_STUDENTS", "All students"
    ALL_PARENTS = "ALL_PARENTS", "All parents"
    CLASS = "CLASS", "Specific class"
    SECTION = "SECTION", "Specific section"
    STUDENT = "STUDENT", "Individual student"


class NotificationTemplate(models.Model):
    school = models.ForeignKey(
        "customers.School",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notification_templates",
        help_text="Null = global template (Super Admin)",
    )
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True)
    channel = models.CharField(max_length=10, choices=NotificationChannel.choices)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(
        help_text="Supports placeholders like {student_name}, {class_name}, {fee_amount}, {due_date}"
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class NotificationLog(models.Model):
    school = models.ForeignKey(
        "customers.School",
        on_delete=models.CASCADE,
        related_name="notification_logs",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_notifications",
    )
    sender_role = models.CharField(max_length=20)
    channel = models.CharField(max_length=10, choices=NotificationChannel.choices)
    target_type = models.CharField(max_length=20, choices=NotificationTargetType.choices)
    target_class = models.ForeignKey(
        "school_data.ClassRoom",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    target_section = models.ForeignKey(
        "school_data.Section",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    target_student = models.ForeignKey(
        "school_data.Student",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    recipient_name = models.CharField(max_length=255, blank=True)
    recipient_phone = models.CharField(max_length=20, blank=True)
    recipient_email = models.EmailField(blank=True)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=[("PENDING", "Pending"), ("SENT", "Sent"), ("FAILED", "Failed")],
        default="PENDING",
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.school.code} - {self.channel} - {self.created_at:%Y-%m-%d %H:%M}"


class SchoolSMSCredit(models.Model):
    school = models.OneToOneField(
        "customers.School",
        on_delete=models.CASCADE,
        related_name="sms_credit",
    )
    balance = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.school.code} – {self.balance} SMS"


class StudentNotificationRead(models.Model):
    """Tracks which notification logs were read by which student."""
    student = models.ForeignKey(
        "school_data.Student",
        on_delete=models.CASCADE,
        related_name="read_notifications",
    )
    notification = models.ForeignKey(
        NotificationLog,
        on_delete=models.CASCADE,
        related_name="student_reads",
    )
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "notification"],
                name="unique_student_notification_read",
            ),
        ]
        ordering = ["-read_at"]

    def __str__(self) -> str:
        return f"{self.student_id} read {self.notification_id}"

