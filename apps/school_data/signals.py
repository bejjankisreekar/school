"""Keep ExamSession.updated_at in sync when papers change."""

from django.db import connection
from django.db.utils import DatabaseError
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Exam


@receiver(post_save, sender=Exam)
@receiver(post_delete, sender=Exam)
def touch_exam_session_on_paper_change(sender, instance, **kwargs):
    sid = getattr(instance, "session_id", None)
    if not sid:
        return
    from .models import ExamSession

    try:
        ExamSession.objects.filter(pk=sid).update(updated_at=timezone.now())
    except DatabaseError:
        try:
            connection.rollback()
        except Exception:
            pass
