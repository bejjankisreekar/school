from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Roles(models.TextChoices):
        SUPERADMIN = "SUPERADMIN", "Super admin"
        ADMIN = "ADMIN", "School admin"
        TEACHER = "TEACHER", "Teacher"
        STUDENT = "STUDENT", "Student"

    role = models.CharField(
        max_length=20,
        choices=Roles.choices,
        default=Roles.STUDENT,
    )
    school = models.ForeignKey(
        "core.School",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        db_index=True,
        to_field="code",
    )
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        help_text="Optional contact phone number.",
    )

    def __str__(self) -> str:
        return f"{self.username} ({self.get_role_display()})"
