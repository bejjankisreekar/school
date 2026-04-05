"""Model signals — exam session touch, fee inheritance for new / moved students."""

from django.db import connection
from django.db.models.signals import post_delete, post_save, pre_save
from django.db.utils import DatabaseError
from django.dispatch import receiver
from django.utils import timezone

from .models import Exam, Student


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


@receiver(pre_save, sender=Student)
def _student_fee_class_track(sender, instance, **kwargs):
    """Remember previous class/section so we only run fee inheritance when those change."""
    if not instance.pk:
        instance._fee_prev_classroom_id = None
        instance._fee_prev_section_id = None
        return
    try:
        old = Student.objects.get(pk=instance.pk)
        instance._fee_prev_classroom_id = old.classroom_id
        instance._fee_prev_section_id = old.section_id
    except Student.DoesNotExist:
        instance._fee_prev_classroom_id = None
        instance._fee_prev_section_id = None


@receiver(post_save, sender=Student)
def student_inherit_class_fee_structures(sender, instance, created, **kwargs):
    """
    New admission or class/section change: create pending Fee rows for active FeeStructure
    lines that apply to this student (same rules as Fee Master auto-assign).
    """
    if not instance.classroom_id:
        return
    if created:
        from apps.core import fee_services

        fee_services.assign_missing_fees_for_student(instance)
        return
    prev_c = getattr(instance, "_fee_prev_classroom_id", None)
    prev_s = getattr(instance, "_fee_prev_section_id", None)
    if prev_c != instance.classroom_id or prev_s != instance.section_id:
        from apps.core import fee_services

        fee_services.assign_missing_fees_for_student(instance)
