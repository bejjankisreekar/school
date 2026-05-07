from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class PlatformMessageThread(models.Model):
    """
    One conversation per school (maps 1:1 with school_id).
    Stored in the public schema.
    """

    school = models.OneToOneField(
        "customers.School",
        on_delete=models.CASCADE,
        related_name="platform_message_thread",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    pinned_at = models.DateTimeField(null=True, blank=True)
    archived_by_superadmin = models.BooleanField(default=False)
    archived_by_school = models.BooleanField(default=False)

    class Meta:
        ordering = ["-pinned_at", "-updated_at"]

    def touch(self):
        self.updated_at = timezone.now()
        self.save(update_fields=["updated_at"])


class PlatformMessage(models.Model):
    class SenderRole(models.TextChoices):
        SUPERADMIN = "superadmin", "Super Admin"
        SCHOOLADMIN = "schooladmin", "School Admin"

    thread = models.ForeignKey(
        PlatformMessageThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender_role = models.CharField(max_length=16, choices=SenderRole.choices)
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_messages_sent",
    )
    body = models.TextField()
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.sender_role} @ {self.created_at:%Y-%m-%d %H:%M}"
