"""
Per-tenant teacher/student/class counts for superadmin verification.
Uses tenant_context + same safe patterns as platform_financials._safe_tenant_footprint.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.db import connection, transaction
from django.db.models import Count, Q
from django.db.utils import DatabaseError
from django_tenants.utils import tenant_context

from apps.customers.models import School
from apps.core.platform_financials import _safe_tenant_footprint
from apps.school_data.models import ClassSectionSubjectTeacher, Student, Teacher


@dataclass
class FootprintSchoolRow:
    school_id: int
    code: str
    name: str
    teachers: int
    students: int
    classes: int


def build_footprint_school_rows(q: str | None = None) -> tuple[int, int, int, list[FootprintSchoolRow]]:
    """
    Returns (total_teachers, total_students, total_classes, per-school rows).
    Optional `q` filters by school code or name (case-insensitive).
    """
    total_teachers = total_students = total_classes = 0
    schools = School.objects.exclude(schema_name="public").order_by("name")
    if q:
        qq = q.strip()
        if qq:
            schools = schools.filter(Q(code__icontains=qq) | Q(name__icontains=qq))

    rows: list[FootprintSchoolRow] = []
    for school in schools:
        t, n, c = _safe_tenant_footprint(school)
        total_teachers += t
        total_students += n
        total_classes += c
        rows.append(
            FootprintSchoolRow(
                school_id=school.pk,
                code=school.code,
                name=school.name,
                teachers=t,
                students=n,
                classes=c,
            )
        )
    return total_teachers, total_students, total_classes, rows


def build_class_section_footprint(school: School) -> list[dict]:
    """
    One row per (classroom, section) bucket with student count and teacher count.
    Teachers: distinct assignments from ClassSectionSubjectTeacher when present;
    else teachers linked to the class via Teacher.classrooms M2M (class-level only).
    Includes a row for students with no class if any.
    """
    try:
        with tenant_context(school):
            with transaction.atomic():
                return _class_section_rows_inner()
    except DatabaseError:
        try:
            if not connection.in_atomic_block:
                connection.rollback()
        except Exception:
            pass
        return []


def _class_section_rows_inner() -> list[dict]:
    rows_out: list[dict] = []

    # Aggregate students by class + section
    groups = (
        Student.objects.values(
            "classroom_id",
            "section_id",
            "classroom__name",
            "classroom__grade_order",
            "section__name",
        )
        .annotate(student_count=Count("id"))
        .order_by("classroom__grade_order", "classroom__name", "section__name")
    )

    for g in groups:
        cid = g["classroom_id"]
        sid = g["section_id"]
        sc = g["student_count"]
        cname = g["classroom__name"] or "—"
        sname = g["section__name"] or "—"

        tc = _teacher_count_for_bucket(cid, sid)

        label = cname
        if sname and sname != "—":
            label = f"{cname} — {sname}"

        rows_out.append(
            {
                "class_name": cname,
                "section_name": sname,
                "label": label,
                "classroom_id": cid,
                "section_id": sid,
                "student_count": sc,
                "teacher_count": tc,
            }
        )

    unassigned_teacher_count = (
        Teacher.objects.annotate(_cc=Count("classrooms")).filter(_cc=0).count()
    )

    if unassigned_teacher_count:
        rows_out.append(
            {
                "class_name": "—",
                "section_name": "—",
                "label": "Teachers (no class assigned)",
                "classroom_id": None,
                "section_id": None,
                "student_count": 0,
                "teacher_count": unassigned_teacher_count,
                "is_meta": True,
            }
        )

    return rows_out


def _teacher_count_for_bucket(classroom_id: int | None, section_id: int | None) -> int:
    if not classroom_id:
        return 0
    if section_id:
        n = (
            ClassSectionSubjectTeacher.objects.filter(class_obj_id=classroom_id, section_id=section_id)
            .values("teacher_id")
            .distinct()
            .count()
        )
        if n > 0:
            return n
    return Teacher.objects.filter(classrooms__id=classroom_id).distinct().count()
