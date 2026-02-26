from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Roles(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        TEACHER = "TEACHER", "Teacher"
        STUDENT = "STUDENT", "Student"

    role = models.CharField(
        max_length=20,
        choices=Roles.choices,
        default=Roles.STUDENT,
    )
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        help_text="Optional contact phone number.",
    )

    def __str__(self) -> str:
        return f"{self.username} ({self.get_role_display()})"
