"""
Super Admin cross-tenant scans for students and teachers (django-tenants).
Uses tenant_context per school; aggregates fee data in-bulk where possible.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Q, Sum
from django.db.utils import DatabaseError
from django_tenants.utils import tenant_context

from apps.customers.models import School
from apps.school_data.models import AcademicYear, ClassRoom, Fee, Payment, Section, Student, Teacher


def _rollback_safe():
    try:
        if not connection.in_atomic_block:
            connection.rollback()
    except Exception:
        pass


def platform_student_totals() -> tuple[int, int, int]:
    """Total, active, inactive student counts across all non-public tenants."""
    total = active = 0
    for school in School.objects.exclude(schema_name="public").order_by("pk"):
        try:
            with tenant_context(school):
                with transaction.atomic():
                    n = Student.objects.count()
                    a = Student.objects.filter(user__is_active=True).count()
                    total += n
                    active += a
        except DatabaseError:
            _rollback_safe()
    inactive = max(0, total - active)
    return total, active, inactive


def platform_teacher_totals() -> tuple[int, int, int]:
    """Total, active, inactive teacher counts across all tenants."""
    total = active = 0
    for school in School.objects.exclude(schema_name="public").order_by("pk"):
        try:
            with tenant_context(school):
                with transaction.atomic():
                    n = Teacher.objects.count()
                    a = Teacher.objects.filter(user__is_active=True).count()
                    total += n
                    active += a
        except DatabaseError:
            _rollback_safe()
    inactive = max(0, total - active)
    return total, active, inactive


def school_filter_choices():
    return School.objects.exclude(schema_name="public").order_by("name")


def tenant_student_fee_maps(student_ids: list[int], today: date) -> tuple[dict, dict, dict, set]:
    """
    Returns (fee_total_by_sid, paid_by_sid, has_overdue_sid set, fee_status_by_sid rough)
    """
    if not student_ids:
        return {}, {}, set(), {}
    fee_total = {
        r["student_id"]: (r["t"] or Decimal("0")).quantize(Decimal("0.01"))
        for r in Fee.objects.filter(student_id__in=student_ids)
        .values("student_id")
        .annotate(t=Sum("amount"))
    }
    paid = {}
    for r in (
        Payment.objects.filter(fee__student_id__in=student_ids)
        .values("fee__student_id")
        .annotate(t=Sum("amount"))
    ):
        sid = r["fee__student_id"]
        paid[sid] = (paid.get(sid, Decimal("0")) + (r["t"] or Decimal("0"))).quantize(Decimal("0.01"))

    overdue_ids: set[int] = set()
    for r in (
        Fee.objects.filter(student_id__in=student_ids, due_date__lt=today)
        .exclude(status="PAID")
        .values_list("student_id", flat=True)
        .distinct()
    ):
        overdue_ids.add(r)

    status_by_sid: dict[int, str] = {}
    for sid in student_ids:
        ft = fee_total.get(sid, Decimal("0"))
        pd = paid.get(sid, Decimal("0"))
        pend = (ft - pd).quantize(Decimal("0.01"))
        if ft <= 0:
            status_by_sid[sid] = "—"
        elif pend <= Decimal("0.01"):
            status_by_sid[sid] = "Paid"
        elif sid in overdue_ids:
            status_by_sid[sid] = "Overdue"
        else:
            status_by_sid[sid] = "Pending"
    return fee_total, paid, overdue_ids, status_by_sid


@dataclass
class GlobalStudentRow:
    school_id: int
    school_code: str
    school_name: str
    student_pk: int
    student_public_id: str
    name: str
    class_name: str
    section_name: str
    academic_year: str
    is_active: bool
    phone: str
    fee_total: Decimal
    fee_paid: Decimal
    fee_pending: Decimal
    fee_status: str
    has_overdue: bool


def collect_global_students(
    *,
    school_id: int | None = None,
    classroom_id: int | None = None,
    section_id: int | None = None,
    academic_year_name: str = "",
    status: str = "",
    search: str = "",
    fee_filter: str = "",
    today: date | None = None,
) -> list[GlobalStudentRow]:
    today = today or date.today()
    search = (search or "").strip()
    schools = School.objects.exclude(schema_name="public").order_by("name")
    if school_id:
        schools = schools.filter(pk=school_id)

    rows: list[GlobalStudentRow] = []
    for school in schools:
        try:
            with tenant_context(school):
                with transaction.atomic():
                    qs = Student.objects.select_related(
                        "user", "classroom", "section", "academic_year"
                    ).all()
                    if classroom_id:
                        qs = qs.filter(classroom_id=classroom_id)
                    if section_id:
                        qs = qs.filter(section_id=section_id)
                    if academic_year_name:
                        qs = qs.filter(academic_year__name__iexact=academic_year_name.strip())
                    if status == "active":
                        qs = qs.filter(user__is_active=True)
                    elif status == "inactive":
                        qs = qs.filter(user__is_active=False)
                    if search:
                        s = search
                        qs = qs.filter(
                            Q(user__first_name__icontains=s)
                            | Q(user__last_name__icontains=s)
                            | Q(user__username__icontains=s)
                            | Q(roll_number__icontains=s)
                            | Q(admission_number__icontains=s)
                            | Q(phone__icontains=s)
                            | Q(parent_phone__icontains=s)
                        )
                    ids = list(qs.values_list("pk", flat=True))
                    if not ids:
                        continue
                    fee_total, paid_map, overdue_set, status_map = tenant_student_fee_maps(ids, today)
                    stu_map = {s.pk: s for s in qs.filter(pk__in=ids)}
                    for sid in ids:
                        st = stu_map[sid]
                        ft = fee_total.get(sid, Decimal("0"))
                        pd = paid_map.get(sid, Decimal("0"))
                        pend = (ft - pd).quantize(Decimal("0.01"))
                        has_od = sid in overdue_set
                        fst = status_map.get(sid, "—")

                        if fee_filter == "pending":
                            if not (ft > 0 and pend > Decimal("0.01")):
                                continue
                        elif fee_filter == "paid":
                            if not (ft > 0 and pend <= Decimal("0.01")):
                                continue
                        elif fee_filter == "overdue":
                            if not has_od:
                                continue

                        name = (st.user.get_full_name() or st.user.username or "").strip()
                        rows.append(
                            GlobalStudentRow(
                                school_id=school.pk,
                                school_code=school.code,
                                school_name=school.name,
                                student_pk=st.pk,
                                student_public_id=f"{school.code}-{st.pk}",
                                name=name,
                                class_name=st.classroom.name if st.classroom else "—",
                                section_name=st.section.name if st.section else "—",
                                academic_year=st.academic_year.name if st.academic_year else "—",
                                is_active=st.user.is_active,
                                phone=(st.phone or st.parent_phone or st.user.phone_number or "")[:20],
                                fee_total=ft,
                                fee_paid=pd,
                                fee_pending=max(Decimal("0"), pend),
                                fee_status=fst,
                                has_overdue=has_od,
                            )
                        )
        except DatabaseError:
            _rollback_safe()
            continue

    return rows


def tenant_dropdowns_for_school(school: School) -> dict:
    """Classrooms, sections, academic year names for filter dropdowns."""
    out = {"classrooms": [], "sections": [], "academic_years": []}
    try:
        with tenant_context(school):
            with transaction.atomic():
                out["classrooms"] = list(
                    ClassRoom.objects.order_by("name").values("id", "name")
                )
                out["sections"] = list(Section.objects.order_by("name").values("id", "name"))
                out["academic_years"] = list(
                    AcademicYear.objects.order_by("-start_date").values_list("name", flat=True)
                )
    except DatabaseError:
        _rollback_safe()
    return out


@dataclass
class GlobalTeacherRow:
    school_id: int
    school_code: str
    school_name: str
    teacher_pk: int
    name: str
    email: str
    phone: str
    subjects: str
    classes_count: int
    is_active: bool


def collect_global_teachers(
    *,
    school_id: int | None = None,
    subject_q: str = "",
    status: str = "",
    search: str = "",
) -> list[GlobalTeacherRow]:
    search = (search or "").strip()
    subject_q = (subject_q or "").strip()
    schools = School.objects.exclude(schema_name="public").order_by("name")
    if school_id:
        schools = schools.filter(pk=school_id)

    rows: list[GlobalTeacherRow] = []
    for school in schools:
        try:
            with tenant_context(school):
                with transaction.atomic():
                    qs = Teacher.objects.select_related("user").prefetch_related(
                        "subjects", "classrooms"
                    )
                    if status == "active":
                        qs = qs.filter(user__is_active=True)
                    elif status == "inactive":
                        qs = qs.filter(user__is_active=False)
                    if search:
                        s = search
                        qs = qs.filter(
                            Q(user__first_name__icontains=s)
                            | Q(user__last_name__icontains=s)
                            | Q(user__username__icontains=s)
                            | Q(phone_number__icontains=s)
                            | Q(employee_id__icontains=s)
                        )
                    if subject_q:
                        qs = qs.filter(
                            Q(subjects__name__icontains=subject_q)
                            | Q(subjects__code__icontains=subject_q)
                            | Q(subject__name__icontains=subject_q)
                        ).distinct()

                    for t in qs.order_by("user__last_name", "user__first_name"):
                        subs = [x.name for x in t.subjects.all()[:8]]
                        subj_str = ", ".join(subs) if subs else (t.subject.name if t.subject else "—")
                        rows.append(
                            GlobalTeacherRow(
                                school_id=school.pk,
                                school_code=school.code,
                                school_name=school.name,
                                teacher_pk=t.pk,
                                name=(t.user.get_full_name() or t.user.username or "").strip(),
                                email=t.user.email or "",
                                phone=t.phone_number or "",
                                subjects=subj_str[:200],
                                classes_count=t.classrooms.count(),
                                is_active=t.user.is_active,
                            )
                        )
        except DatabaseError:
            _rollback_safe()
            continue

    return rows


def sort_student_rows(rows: list[GlobalStudentRow], sort: str) -> None:
    if sort == "school":
        rows.sort(key=lambda r: (r.school_name.lower(), r.name.lower()))
    elif sort == "fee_pending":
        rows.sort(key=lambda r: r.fee_pending, reverse=True)
    elif sort == "class":
        rows.sort(key=lambda r: (r.class_name.lower(), r.name.lower()))
    else:
        rows.sort(key=lambda r: r.name.lower())


def sort_teacher_rows(rows: list[GlobalTeacherRow], sort: str) -> None:
    if sort == "school":
        rows.sort(key=lambda r: (r.school_name.lower(), r.name.lower()))
    elif sort == "classes":
        rows.sort(key=lambda r: r.classes_count, reverse=True)
    else:
        rows.sort(key=lambda r: r.name.lower())
