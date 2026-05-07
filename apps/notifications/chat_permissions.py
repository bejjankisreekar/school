from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q

from apps.school_data.models import ClassSectionSubjectTeacher, ClassSectionTeacher, Student, Teacher

User = get_user_model()


def get_allowed_chat_user_ids(user) -> set[int]:
    role = getattr(user, "role", None)
    if role in (User.Roles.ADMIN, User.Roles.SUPERADMIN):
        qs = User.objects.filter(is_active=True)
        if getattr(user, "school", None):
            qs = qs.filter(school=user.school)
        return set(qs.exclude(id=user.id).values_list("id", flat=True))

    if role == User.Roles.STUDENT:
        student = getattr(user, "student_profile", None)
        if not student:
            return set()
        teacher_user_ids = set(
            ClassSectionSubjectTeacher.objects.filter(
                class_obj=student.classroom,
                section=student.section,
            ).values_list("teacher__user_id", flat=True)
        )
        return set(i for i in teacher_user_ids if i and i != user.id)

    if role == User.Roles.TEACHER:
        teacher = getattr(user, "teacher_profile", None)
        if not teacher:
            return set()
        allowed_students = Student.objects.none()
        class_ids = list(teacher.classrooms.values_list("id", flat=True))
        if class_ids:
            allowed_students = allowed_students | Student.objects.filter(classroom_id__in=class_ids)

        section_pairs = list(
            ClassSectionTeacher.objects.filter(teacher=teacher).values_list("classroom_id", "section_id")
        )
        if section_pairs:
            pair_filter = Q()
            for classroom_id, section_id in section_pairs:
                pair_filter |= Q(classroom_id=classroom_id, section_id=section_id)
            allowed_students = allowed_students | Student.objects.filter(pair_filter)

        mapping_pairs = list(
            ClassSectionSubjectTeacher.objects.filter(teacher=teacher).values_list("class_obj_id", "section_id")
        )
        if mapping_pairs:
            pair_filter = Q()
            for classroom_id, section_id in mapping_pairs:
                pair_filter |= Q(classroom_id=classroom_id, section_id=section_id)
            allowed_students = allowed_students | Student.objects.filter(pair_filter)

        return set(allowed_students.values_list("user_id", flat=True))

    return set()


def can_user_message(sender, receiver) -> bool:
    if not sender or not receiver or sender.id == receiver.id:
        return False
    if not sender.is_authenticated or not receiver.is_active:
        return False
    if getattr(sender, "school", None) != getattr(receiver, "school", None):
        return False
    return receiver.id in get_allowed_chat_user_ids(sender)
