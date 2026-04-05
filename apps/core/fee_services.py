"""Aggregations and helpers for school fee / billing dashboard."""

from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum

from apps.school_data.models import ClassRoom, Fee, FeeStructure, Payment, Section, Student


def ledger_section_filter_choices():
    """
    Unique sections for fee hub ledger filter (label = section name only).
    classroom_ids_csv lists ClassRoom ids linked via M2M for dependent class dropdown.
    """
    result = []
    for sec in Section.objects.prefetch_related("classrooms").order_by("name"):
        ids = [str(c.id) for c in sec.classrooms.all()]
        result.append(
            {
                "id": sec.id,
                "name": sec.name,
                "classroom_ids_csv": ",".join(ids),
            }
        )
    return result


def section_class_pairs():
    """
    Each row: section id, section name, owning class id/name.
    Section links to ClassRoom via M2M (classroom.sections), not a FK on Section.
    """
    pairs = []
    for classroom in ClassRoom.objects.prefetch_related("sections").order_by("name"):
        for sec in classroom.sections.all():
            pairs.append(
                {
                    "id": sec.id,
                    "name": sec.name,
                    "classroom_id": classroom.id,
                    "class_name": classroom.name,
                }
            )
    return pairs


def fee_amount_paid(fee: Fee) -> Decimal:
    total = Payment.objects.filter(fee=fee).aggregate(s=Sum("amount"))["s"]
    return total if total is not None else Decimal("0")


def fee_balance(fee: Fee) -> Decimal:
    """Remaining balance after payments, net of concessions."""
    paid = fee_amount_paid(fee)
    return max(Decimal("0"), fee.effective_due_amount - paid)


def fee_line_collection_status(fee: Fee) -> str:
    """PAID | PARTIAL | DUE — fee collection / invoice row badges."""
    bal = fee_balance(fee)
    if bal <= 0:
        return "PAID"
    if fee_amount_paid(fee) > 0:
        return "PARTIAL"
    return "DUE"


def fee_ui_status(fee: Fee) -> str:
    """PAID | PARTIAL | OVERDUE | PENDING — for ledger display."""
    bal = fee_balance(fee)
    if bal <= 0:
        return "PAID"
    paid = fee_amount_paid(fee)
    today = date.today()
    if fee.due_date < today:
        return "OVERDUE"
    if paid > 0:
        return "PARTIAL"
    return "PENDING"


def fees_queryset_for_year(academic_year):
    qs = Fee.objects.all()
    if academic_year is not None:
        qs = qs.filter(academic_year=academic_year)
    return qs


def build_kpis(academic_year) -> dict:
    today = date.today()
    students_qs = Student.objects.all()
    total_students = students_qs.count()

    fee_qs = fees_queryset_for_year(academic_year)
    total_billed = fee_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")

    if academic_year is not None:
        paid_total = (
            Payment.objects.filter(fee__academic_year=academic_year).aggregate(s=Sum("amount"))["s"] or Decimal("0")
        )
    else:
        paid_total = Payment.objects.aggregate(s=Sum("amount"))["s"] or Decimal("0")

    pending_total = Decimal("0")
    for f in fee_qs:
        b = fee_balance(f)
        if b > 0:
            pending_total += b

    overdue_amount = Decimal("0")
    overdue_fees = fee_qs.filter(due_date__lt=today).exclude(status="PAID")
    for f in overdue_fees:
        b = fee_balance(f)
        if b > 0:
            overdue_amount += b

    today_coll = (
        Payment.objects.filter(payment_date=today).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    month_start = today.replace(day=1)
    month_coll = (
        Payment.objects.filter(payment_date__gte=month_start, payment_date__lte=today).aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )

    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    prev_coll = (
        Payment.objects.filter(
            payment_date__gte=last_month_start,
            payment_date__lte=last_month_end,
        ).aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )

    trend_pct = None
    if prev_coll > 0:
        trend_pct = float((month_coll - prev_coll) / prev_coll * 100)

    defaulter_ids = set()
    for f in fee_qs.filter(due_date__lt=today).exclude(status="PAID"):
        if fee_balance(f) > 0:
            defaulter_ids.add(f.student_id)
    defaulters_count = len(defaulter_ids)

    return {
        "total_students": total_students,
        "total_billed": total_billed,
        "total_collected": paid_total,
        "total_pending": pending_total,
        "overdue_amount": overdue_amount,
        "today_collections": today_coll,
        "month_collections": month_coll,
        "month_trend_pct": trend_pct,
        "defaulters_count": defaulters_count,
    }


def chart_monthly_collections(months: int = 8) -> tuple[list[str], list[float]]:
    """Labels (Mon YYYY) and amounts for bar chart."""
    today = date.today()
    labels = []
    amounts = []
    for i in range(months - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            m += 12
            y -= 1
        last_d = monthrange(y, m)[1]
        start = date(y, m, 1)
        end = date(y, m, last_d)
        amt = Payment.objects.filter(payment_date__gte=start, payment_date__lte=end).aggregate(s=Sum("amount"))["s"]
        labels.append(start.strftime("%b %Y"))
        amounts.append(float(amt or 0))
    return labels, amounts


def chart_paid_vs_pending(academic_year) -> dict:
    fee_qs = fees_queryset_for_year(academic_year)
    if academic_year is not None:
        paid = Payment.objects.filter(fee__academic_year=academic_year).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    else:
        paid = Payment.objects.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    pending = Decimal("0")
    for f in fee_qs:
        b = fee_balance(f)
        if b > 0:
            pending += b
    return {
        "paid": float(paid),
        "pending": float(pending),
    }


def chart_class_revenue(academic_year) -> tuple[list[str], list[float]]:
    """Sum of fee.amount billed per class name."""
    fee_qs = fees_queryset_for_year(academic_year).select_related("student__classroom")
    buckets: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for f in fee_qs:
        cname = f.student.classroom.name if f.student.classroom_id else "Unassigned"
        buckets[cname] += f.amount
    items = sorted(buckets.items(), key=lambda x: -x[1])
    labels = [x[0] for x in items[:12]]
    values = [float(x[1]) for x in items[:12]]
    return labels, values


def structure_table_rows(active_only: bool = False):
    """For fee structure grid."""
    qs = FeeStructure.objects.select_related("fee_type", "classroom", "academic_year").order_by(
        "classroom__name", "fee_type__name"
    )
    if active_only:
        qs = qs.filter(is_active=True)
    rows = []
    for s in qs:
        rows.append(
            {
                "obj": s,
                "class_name": s.classroom.name if s.classroom_id else "—",
                "fee_type_name": s.fee_type.name,
                "amount": s.amount,
                "frequency": s.get_frequency_display(),
                "due_day": s.due_day_of_month or "—",
                "status": "Active" if s.is_active else "Inactive",
                "year": s.academic_year.name if s.academic_year_id else "—",
            }
        )
    return rows


def build_fee_structure_class_summaries(academic_year):
    """
    Per-class cards for fee hub: structure totals, collection stats, detail payloads
    (breakdown lines, student counts, preview rows). Scoped to academic_year when set.
    """
    classroom_qs = ClassRoom.objects.prefetch_related("sections").order_by("name")
    if academic_year is not None:
        classroom_qs = classroom_qs.filter(academic_year_id=academic_year.id)

    struct_qs = (
        FeeStructure.objects.filter(is_active=True)
        .select_related("fee_type", "classroom", "academic_year")
        .order_by("fee_type__name", "id")
    )
    if academic_year is not None:
        struct_qs = struct_qs.filter(academic_year_id=academic_year.id)

    by_class: dict[int, list[FeeStructure]] = defaultdict(list)
    for s in struct_qs:
        if s.classroom_id:
            by_class[s.classroom_id].append(s)

    fee_qs = fees_queryset_for_year(academic_year).select_related(
        "fee_structure", "fee_structure__fee_type", "student__user", "student__section"
    ).prefetch_related("payments")
    fees_list = list(fee_qs)

    fees_by_classroom: dict[int, list[Fee]] = defaultdict(list)
    for f in fees_list:
        cid = f.student.classroom_id
        if cid:
            fees_by_classroom[cid].append(f)

    class_ids = [c.id for c in classroom_qs]
    student_counts: dict[int, int] = {}
    if class_ids:
        sc_rows = (
            Student.objects.filter(classroom_id__in=class_ids)
            .values("classroom_id")
            .annotate(n=Count("id"))
        )
        student_counts = {r["classroom_id"]: r["n"] for r in sc_rows}

    summaries: list[dict] = []
    for classroom in classroom_qs:
        cid = classroom.id
        structures = list(by_class.get(cid, []))
        section_parts = [x.name for x in classroom.sections.all()]
        sections_label = ", ".join(section_parts) if section_parts else "—"

        total_per_student = sum((s.amount for s in structures), Decimal("0"))
        struct_ids = {s.id for s in structures}

        cf = fees_by_classroom.get(cid, [])
        total_expected = sum((x.effective_due_amount for x in cf), Decimal("0"))
        total_discount_amt = sum((x.total_concession_amount for x in cf), Decimal("0"))
        collected = sum((fee_amount_paid(x) for x in cf), Decimal("0"))
        pending = sum((fee_balance(x) for x in cf), Decimal("0"))

        fees_for_struct = [f for f in cf if f.fee_structure_id in struct_ids] if struct_ids else []
        stu_with_struct_fees = {f.student_id for f in fees_for_struct}
        applied_count = len(stu_with_struct_fees)

        discount_students = set()
        for f in fees_for_struct:
            if f.total_concession_amount and f.total_concession_amount > 0:
                discount_students.add(f.student_id)

        paid_students: set[int] = set()
        pending_students: set[int] = set()
        by_stu: dict[int, list[Fee]] = defaultdict(list)
        for f in fees_for_struct:
            by_stu[f.student_id].append(f)
        for sid, fl in by_stu.items():
            if all(fee_balance(f) <= 0 for f in fl):
                paid_students.add(sid)
            else:
                pending_students.add(sid)

        if total_expected > 0 and pending <= 0:
            status_key = "clear"
            status_label = "Fully collected"
        elif pending > 0:
            status_key = "outstanding"
            status_label = "Outstanding"
        elif not structures:
            status_key = "no_structure"
            status_label = "No structure"
        elif applied_count == 0:
            status_key = "not_applied"
            status_label = "Not applied"
        else:
            status_key = "neutral"
            status_label = "—"

        breakdown = [
            {
                "id": s.id,
                "fee_type_name": s.fee_type.name,
                "amount": s.amount,
                "frequency": s.get_frequency_display(),
                "due_day": s.due_day_of_month,
            }
            for s in structures
        ]

        stu_fees_map: dict[int, list[Fee]] = defaultdict(list)
        for f in cf:
            stu_fees_map[f.student_id].append(f)

        preview_rows: list[dict] = []
        students_qs = (
            Student.objects.filter(classroom_id=cid)
            .select_related("user", "section")
            .order_by("user__last_name", "user__first_name", "user__username")
        )
        for st in students_qs:
            fl = stu_fees_map.get(st.id, [])
            if not fl:
                continue
            tot_amt = sum((f.amount for f in fl), Decimal("0"))
            disc = sum((f.total_concession_amount for f in fl), Decimal("0"))
            final_due = sum((f.effective_due_amount for f in fl), Decimal("0"))
            paid_sum = sum((fee_amount_paid(f) for f in fl), Decimal("0"))
            bal = sum((fee_balance(f) for f in fl), Decimal("0"))
            nm = st.user.get_full_name() or st.user.username or "—"
            preview_rows.append(
                {
                    "name": nm,
                    "section": st.section.name if st.section_id else "—",
                    "total_due": tot_amt,
                    "discount": disc,
                    "final_due": final_due,
                    "paid": paid_sum,
                    "balance": bal,
                }
            )
        preview_rows.sort(key=lambda r: (-r["balance"], r["name"].lower()))
        preview_rows = preview_rows[:50]

        summaries.append(
            {
                "classroom_id": cid,
                "class_name": classroom.name,
                "sections_label": sections_label,
                "student_count": student_counts.get(cid, 0),
                "total_per_student": total_per_student,
                "total_expected": total_expected,
                "total_discount_amt": total_discount_amt,
                "collected": collected,
                "pending": pending,
                "status_key": status_key,
                "status_label": status_label,
                "breakdown": breakdown,
                "applied_students": applied_count,
                "discount_students_count": len(discount_students),
                "paid_students_count": len(paid_students),
                "pending_students_count": len(pending_students),
                "preview_rows": preview_rows,
                "structures": structures,
            }
        )

    return summaries


def default_due_date_for_structure(structure: FeeStructure) -> date:
    """Explicit first due date on the structure, else due-day-of-month logic, else month-end."""
    if getattr(structure, "first_due_date", None):
        return structure.first_due_date
    today = date.today()
    dom = structure.due_day_of_month
    if dom:
        y, m = today.year, today.month
        last = monthrange(y, m)[1]
        day = min(int(dom), last)
        candidate = date(y, m, day)
        if candidate < today:
            if m == 12:
                y, m = y + 1, 1
            else:
                m += 1
            last = monthrange(y, m)[1]
            day = min(int(dom), last)
            candidate = date(y, m, day)
        return candidate
    last = monthrange(today.year, today.month)[1]
    return date(today.year, today.month, last)


def apply_structure_to_students(structure: FeeStructure, due_date: date, section_id: int | None = None) -> tuple[int, str | None]:
    """Create Fee rows for all students in structure's class (optional section). Returns (created_count, error)."""
    if not structure.classroom_id:
        return 0, "Assign a class to this fee structure before applying."
    students = Student.objects.filter(
        classroom_id=structure.classroom_id,
        user__is_active=True,
    )
    eff_section = section_id
    if eff_section is None and getattr(structure, "section_id", None):
        eff_section = structure.section_id
    if eff_section:
        students = students.filter(section_id=eff_section)
    ay = structure.academic_year
    created = 0
    for st in students.iterator():
        _, was_created = Fee.objects.get_or_create(
            student=st,
            fee_structure=structure,
            due_date=due_date,
            defaults={
                "amount": structure.amount,
                "academic_year": ay,
                "status": "PENDING",
            },
        )
        if was_created:
            created += 1
    return created, None


def auto_assign_fees_for_structure(structure: FeeStructure) -> tuple[int, str | None]:
    """Create pending Fee rows for all matching students after a fee structure is saved (class-wide or section)."""
    if not structure.classroom_id or not structure.is_active:
        return 0, None
    due = default_due_date_for_structure(structure)
    return apply_structure_to_students(structure, due, section_id=None)


def fee_structures_applicable_to_student(student: Student):
    """Active fee heads for the student's class, respecting optional section scope on the structure."""
    if not student.classroom_id:
        return FeeStructure.objects.none()
    qs = FeeStructure.objects.filter(classroom_id=student.classroom_id, is_active=True)
    if student.section_id:
        return qs.filter(Q(section__isnull=True) | Q(section_id=student.section_id))
    return qs.filter(section__isnull=True)


def ensure_fee_row_for_student(
    structure: FeeStructure,
    student: Student,
    due_date: date,
    fee_academic_year=None,
) -> bool:
    """If the student matches the structure scope, create a pending Fee when missing."""
    if not structure.is_active or not structure.classroom_id:
        return False
    if student.classroom_id != structure.classroom_id:
        return False
    if structure.section_id and student.section_id != structure.section_id:
        return False
    eff_ay = structure.academic_year if structure.academic_year_id else fee_academic_year
    _, was_created = Fee.objects.get_or_create(
        student=student,
        fee_structure=structure,
        due_date=due_date,
        defaults={
            "amount": structure.amount,
            "academic_year": eff_ay,
            "status": "PENDING",
        },
    )
    return was_created


def count_students_impacted_by_class_section(
    classroom_id: int | None,
    section_id: int | None = None,
) -> int:
    """Active users only — matches auto-assign scope."""
    if not classroom_id:
        return 0
    qs = Student.objects.filter(classroom_id=classroom_id, user__is_active=True)
    if section_id:
        qs = qs.filter(section_id=section_id)
    return qs.count()


def assign_missing_fees_for_student(student: Student, academic_year=None) -> int:
    """
    New admissions / class moves / backfill: add pending Fee rows for applicable structures.

    If ``academic_year`` is set, only structures for that year (or with no year on the
    structure) are considered; fee rows use the structure's year or fall back to it.
    """
    if not student.classroom_id:
        return 0
    if not student.user_id or not getattr(student.user, "is_active", True):
        return 0
    qs = fee_structures_applicable_to_student(student)
    if academic_year is not None:
        qs = qs.filter(Q(academic_year_id=academic_year.id) | Q(academic_year__isnull=True))
    n = 0
    for structure in qs:
        due = default_due_date_for_structure(structure)
        if ensure_fee_row_for_student(structure, student, due, fee_academic_year=academic_year):
            n += 1
    return n


def default_pending_fee_for_student(student: Student, academic_year=None) -> Fee | None:
    """First fee line with a positive balance (for collection desk default)."""
    qs = Fee.objects.filter(student=student, status__in=["PENDING", "PARTIAL"]).select_related(
        "fee_structure__fee_type", "academic_year"
    )
    if academic_year is not None:
        qs = qs.filter(academic_year_id=academic_year.id)
    for f in qs.order_by("due_date", "id"):
        if fee_balance(f) > 0:
            return f
    return None


def build_student_ledger_summaries(academic_year, search_q: str = ""):
    """List of dicts for expandable student fee ledger."""
    students = Student.objects.select_related("user", "classroom", "section").order_by(
        "classroom__name", "section__name", "user__last_name", "user__first_name"
    )
    if search_q:
        q = search_q.strip()
        from django.db.models import Q

        students = students.filter(
            Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(user__username__icontains=q)
            | Q(admission_number__icontains=q)
            | Q(roll_number__icontains=q)
        )

    fee_qs = Fee.objects.all().select_related("fee_structure__fee_type").prefetch_related("payments")
    if academic_year is not None:
        fee_qs = fee_qs.filter(academic_year=academic_year)

    fees_by_student: dict[int, list[Fee]] = defaultdict(list)
    for f in fee_qs:
        fees_by_student[f.student_id].append(f)

    summaries = []
    for s in students:
        flist = fees_by_student.get(s.id, [])
        total_fee = sum((f.effective_due_amount for f in flist), Decimal("0"))
        paid_sum = sum((fee_amount_paid(f) for f in flist), Decimal("0"))
        pending = sum((fee_balance(f) for f in flist), Decimal("0"))

        last_payment = None
        for f in flist:
            for p in f.payments.all():
                if last_payment is None or p.payment_date > last_payment:
                    last_payment = p.payment_date

        next_due = None
        for f in sorted(flist, key=lambda x: x.due_date):
            if fee_balance(f) > 0:
                next_due = f.due_date
                break

        if not flist and not search_q:
            continue

        lines = []
        for f in sorted(flist, key=lambda x: (x.due_date, x.fee_structure.fee_type.name)):
            st = fee_ui_status(f)
            lines.append(
                {
                    "label": f.fee_structure.fee_type.name,
                    "amount": f.amount,
                    "discount": f.total_concession_amount,
                    "effective": f.effective_due_amount,
                    "paid": fee_amount_paid(f),
                    "balance": fee_balance(f),
                    "due_date": f.due_date,
                    "status": st,
                }
            )

        if pending <= 0:
            overall = "PAID"
        elif any(fee_ui_status(f) == "OVERDUE" for f in flist):
            overall = "OVERDUE"
        elif any(fee_ui_status(f) == "PARTIAL" for f in flist):
            overall = "PARTIAL"
        else:
            overall = "PENDING"

        summaries.append(
            {
                "student": s,
                "class_section": f"{s.classroom.name if s.classroom else '—'} / {s.section.name if s.section else '—'}",
                "total_fee": total_fee,
                "paid": paid_sum,
                "pending": pending,
                "last_payment": last_payment,
                "next_due": next_due,
                "overall_status": overall,
                "lines": lines,
            }
        )
    return summaries


def build_filtered_fee_ledger(
    academic_year,
    *,
    classroom_id: int | None = None,
    section_id: int | None = None,
    search_q: str = "",
    status_filter: str | None = None,
    fee_type_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    payment_mode: str | None = None,
) -> tuple[list[dict], dict, dict]:
    """
    Filtered student fee ledger rows + totals + chart payloads (filtered set).

    status_filter: PAID | PARTIAL | DUE | OVERDUE (DUE = not overdue, nothing paid on any line).
    Date range applies to Fee.due_date. Payment mode restricts to students with a matching Payment
    on a fee in the current fee queryset (same academic year scope).
    """
    today = date.today()

    fee_qs = Fee.objects.all().select_related("fee_structure__fee_type", "student").prefetch_related("payments")
    if academic_year is not None:
        fee_qs = fee_qs.filter(academic_year=academic_year)
    if fee_type_id:
        fee_qs = fee_qs.filter(fee_structure__fee_type_id=int(fee_type_id))
    if date_from:
        fee_qs = fee_qs.filter(due_date__gte=date_from)
    if date_to:
        fee_qs = fee_qs.filter(due_date__lte=date_to)

    fee_list = list(fee_qs)
    fees_by_student: dict[int, list[Fee]] = defaultdict(list)
    for f in fee_list:
        fees_by_student[f.student_id].append(f)

    student_ids_mode: set[int] | None = None
    if payment_mode and (pm := payment_mode.strip()):
        pay_fees = {f.id for f in fee_list}
        if pay_fees:
            pids = (
                Payment.objects.filter(fee_id__in=pay_fees)
                .filter(payment_method__iexact=pm)
                .values_list("fee__student_id", flat=True)
                .distinct()
            )
            student_ids_mode = set(pids)
        else:
            student_ids_mode = set()

    students = Student.objects.select_related("user", "classroom", "section").order_by(
        "classroom__name", "section__name", "user__last_name", "user__first_name"
    )
    if classroom_id:
        students = students.filter(classroom_id=classroom_id)
    if section_id:
        students = students.filter(section_id=section_id)
    if search_q:
        q = search_q.strip()
        students = students.filter(
            Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(user__username__icontains=q)
            | Q(admission_number__icontains=q)
            | Q(roll_number__icontains=q)
        )
    if student_ids_mode is not None:
        students = students.filter(pk__in=student_ids_mode)

    rows: list[dict] = []
    for s in students:
        flist = fees_by_student.get(s.id, [])
        if not flist and not search_q.strip():
            continue

        total_fee = sum((f.effective_due_amount for f in flist), Decimal("0"))
        total_discount = sum((f.total_concession_amount for f in flist), Decimal("0"))
        paid_sum = sum((fee_amount_paid(f) for f in flist), Decimal("0"))
        pending = sum((fee_balance(f) for f in flist), Decimal("0"))

        last_payment = None
        for f in flist:
            for p in f.payments.all():
                if last_payment is None or p.payment_date > last_payment:
                    last_payment = p.payment_date

        next_due = None
        for f in sorted(flist, key=lambda x: x.due_date):
            if fee_balance(f) > 0:
                next_due = f.due_date
                break

        lines = []
        collect_fee_id = None
        for f in sorted(flist, key=lambda x: (x.due_date, x.fee_structure.fee_type.name)):
            bal = fee_balance(f)
            if collect_fee_id is None and bal > 0:
                collect_fee_id = f.id
            line_st = fee_line_collection_status(f)
            lines.append(
                {
                    "label": f.fee_structure.fee_type.name,
                    "amount": f.amount,
                    "discount": f.total_concession_amount,
                    "effective": f.effective_due_amount,
                    "paid": fee_amount_paid(f),
                    "balance": bal,
                    "due_date": f.due_date,
                    "status": fee_ui_status(f),
                    "line_status": line_st,
                }
            )

        if pending <= 0:
            overall = "PAID"
        elif any(fee_ui_status(f) == "OVERDUE" for f in flist):
            overall = "OVERDUE"
        elif any(fee_ui_status(f) == "PARTIAL" for f in flist):
            overall = "PARTIAL"
        else:
            overall = "PENDING"

        if status_filter:
            sf = status_filter.strip().upper()
            if sf == "DUE" and overall != "PENDING":
                continue
            if sf == "PAID" and overall != "PAID":
                continue
            if sf == "PARTIAL" and overall != "PARTIAL":
                continue
            if sf == "OVERDUE" and overall != "OVERDUE":
                continue

        if not flist and search_q.strip():
            overall = "PENDING"
            if status_filter and status_filter.strip().upper() != "DUE":
                continue

        class_name = s.classroom.name if s.classroom_id else "—"
        sec_name = s.section.name if s.section_id else "—"
        rows.append(
            {
                "student": s,
                "class_name": class_name,
                "section_name": sec_name,
                "class_section": f"{class_name} / {sec_name}",
                "admission": (s.admission_number or "").strip() or "—",
                "total_fee": total_fee,
                "total_discount": total_discount,
                "paid": paid_sum,
                "pending": pending,
                "last_payment": last_payment,
                "next_due": next_due,
                "overall_status": overall,
                "lines": lines,
                "collect_fee_id": collect_fee_id or (flist[0].id if flist else None),
            }
        )

    totals = {
        "student_count": len(rows),
        "total_fee": sum((r["total_fee"] for r in rows), Decimal("0")),
        "total_discount": sum((r["total_discount"] for r in rows), Decimal("0")),
        "paid": sum((r["paid"] for r in rows), Decimal("0")),
        "pending": sum((r["pending"] for r in rows), Decimal("0")),
    }

    pie_paid = float(totals["paid"])
    pie_pending = float(totals["pending"])

    class_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for r in rows:
        class_totals[r["class_name"]] += r["total_fee"]
    class_sorted = sorted(class_totals.items(), key=lambda x: -x[1])
    class_labels = [x[0] for x in class_sorted[:14]]
    class_values = [float(x[1]) for x in class_sorted[:14]]

    overdue_by_class: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["overall_status"] == "OVERDUE":
            overdue_by_class[r["class_name"]] += 1
    oc_sorted = sorted(overdue_by_class.items(), key=lambda x: -x[1])
    overdue_labels = [x[0] for x in oc_sorted[:12]]
    overdue_values = [float(x[1]) for x in oc_sorted[:12]]

    charts = {
        "pie_paid": pie_paid,
        "pie_pending": pie_pending,
        "class_labels": class_labels,
        "class_values": class_values,
        "overdue_labels": overdue_labels,
        "overdue_values": overdue_values,
    }
    return rows, totals, charts


def build_defaulters(academic_year, limit: int = 50) -> list[dict]:
    today = date.today()
    fee_qs = Fee.objects.filter(due_date__lt=today).exclude(status="PAID").select_related(
        "student__user", "student__classroom", "student__section"
    )
    if academic_year is not None:
        fee_qs = fee_qs.filter(academic_year=academic_year)

    by_student: dict[int, dict] = {}
    for f in fee_qs:
        bal = fee_balance(f)
        if bal <= 0:
            continue
        sid = f.student_id
        if sid not in by_student:
            by_student[sid] = {
                "student": f.student,
                "amount": Decimal("0"),
                "oldest_due": f.due_date,
            }
        by_student[sid]["amount"] += bal
        if f.due_date < by_student[sid]["oldest_due"]:
            by_student[sid]["oldest_due"] = f.due_date

    rows = []
    for sid, data in by_student.items():
        days = (today - data["oldest_due"]).days
        rows.append(
            {
                "student": data["student"],
                "class_name": data["student"].classroom.name if data["student"].classroom_id else "—",
                "due_amount": data["amount"],
                "days_overdue": days,
            }
        )
    rows.sort(key=lambda x: -x["days_overdue"])
    return rows[:limit]


def payment_history_rows(
    academic_year,
    class_id=None,
    section_id=None,
    student_id=None,
    fee_type_id=None,
    status_filter=None,
    date_from=None,
    date_to=None,
    limit: int = 200,
):
    qs = Payment.objects.select_related(
        "fee__student__user",
        "fee__student__classroom",
        "fee__student__section",
        "fee__fee_structure__fee_type",
        "received_by",
    ).order_by("-payment_date", "-id")
    if academic_year is not None:
        qs = qs.filter(fee__academic_year=academic_year)
    if class_id:
        qs = qs.filter(fee__student__classroom_id=class_id)
    if section_id:
        qs = qs.filter(fee__student__section_id=section_id)
    if student_id:
        qs = qs.filter(fee__student_id=student_id)
    if fee_type_id:
        qs = qs.filter(fee__fee_structure__fee_type_id=fee_type_id)
    if date_from:
        qs = qs.filter(payment_date__gte=date_from)
    if date_to:
        qs = qs.filter(payment_date__lte=date_to)

    rows = []
    for p in qs[: limit * 2]:
        fee = p.fee
        stu = fee.student
        ui = fee_ui_status(fee)
        if status_filter:
            sf = status_filter.upper()
            if sf == "PAID" and ui != "PAID":
                continue
            if sf == "PENDING" and ui != "PENDING":
                continue
            if sf == "PARTIAL" and ui != "PARTIAL":
                continue
            if sf == "OVERDUE" and ui != "OVERDUE":
                continue
        rows.append(
            {
                "payment": p,
                "receipt_no": p.receipt_number or f"P-{p.id}",
                "student": stu,
                "class_name": stu.classroom.name if stu.classroom_id else "—",
                "fee_type": fee.fee_structure.fee_type.name,
                "amount": p.amount,
                "mode": p.payment_method,
                "date": p.payment_date,
                "status": ui,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def count_students_with_any_pending_fee(academic_year) -> int:
    """Students who have at least one fee line with a positive balance."""
    fee_qs = fees_queryset_for_year(academic_year).prefetch_related("payments")
    pending_ids: set[int] = set()
    for f in fee_qs:
        if fee_balance(f) > 0:
            pending_ids.add(f.student_id)
    return len(pending_ids)


def total_concessions_on_fees(academic_year) -> Decimal:
    """Sum of per-line concession amounts (discounts / scholarships recorded on Fee)."""
    fee_qs = fees_queryset_for_year(academic_year)
    return sum((f.total_concession_amount for f in fee_qs if f.total_concession_amount), Decimal("0"))


def chart_collection_by_fee_type(academic_year, limit: int = 10) -> tuple[list[str], list[float]]:
    """YTD / scoped: total payments grouped by fee type name (for category bar chart)."""
    qs = Payment.objects.filter(fee__fee_structure__fee_type__isnull=False)
    if academic_year is not None:
        qs = qs.filter(fee__academic_year=academic_year)
    rows = (
        qs.values("fee__fee_structure__fee_type__name")
        .annotate(s=Sum("amount"))
        .order_by("-s")[:limit]
    )
    labels = [r["fee__fee_structure__fee_type__name"] or "—" for r in rows]
    values = [float(r["s"] or 0) for r in rows]
    return labels, values


def build_class_fee_structure_cards(academic_year):
    """
    One card per class: sum of active structure line amounts and a breakdown by fee head.
    Section-specific lines are labeled with the section name.
    """
    qs = (
        FeeStructure.objects.filter(is_active=True, classroom_id__isnull=False)
        .select_related("classroom", "section", "fee_type", "academic_year")
        .order_by("classroom__name", "section__name", "fee_type__name", "id")
    )
    if academic_year is not None:
        qs = qs.filter(Q(academic_year=academic_year) | Q(academic_year__isnull=True))
    by_class: dict[int, list] = defaultdict(list)
    for s in qs:
        by_class[s.classroom_id].append(s)
    cards = []
    for cid in sorted(by_class.keys(), key=lambda x: by_class[x][0].classroom.name):
        structs = by_class[cid]
        classroom = structs[0].classroom
        lines = []
        total = Decimal("0")
        for st in structs:
            label = (st.line_name or "").strip() or st.fee_type.name
            if st.section_id:
                sec_name = st.section.name if st.section_id else ""
                if sec_name:
                    label = f"{label} ({sec_name})"
            lines.append(
                {
                    "label": label,
                    "fee_type_name": st.fee_type.name,
                    "amount": st.amount,
                }
            )
            total += st.amount or Decimal("0")
        cards.append(
            {
                "classroom": classroom,
                "classroom_id": cid,
                "lines": lines,
                "structure_total": total,
                "line_count": len(lines),
            }
        )
    return cards


def build_classroom_student_fee_rollups(classroom_id: int, academic_year):
    """
    Active students in the class with aggregated Fee rows: gross, concession, net, paid, pending.
    """
    students = list(
        Student.objects.filter(classroom_id=classroom_id, user__is_active=True)
        .select_related("user", "section", "classroom")
        .order_by("user__last_name", "user__first_name", "admission_number")
    )
    for stu in students:
        assign_missing_fees_for_student(stu, academic_year)
    fee_qs = (
        Fee.objects.filter(student__classroom_id=classroom_id)
        .select_related("fee_structure__fee_type", "student")
        .prefetch_related("payments")
    )
    if academic_year is not None:
        fee_qs = fee_qs.filter(academic_year=academic_year)
    by_student: dict[int, list] = defaultdict(list)
    for f in fee_qs:
        by_student[f.student_id].append(f)
    rows = []
    for stu in students:
        flist = by_student.get(stu.id, [])
        gross = sum((f.amount for f in flist), Decimal("0"))
        concession = sum((f.total_concession_amount for f in flist), Decimal("0"))
        net_due = sum((f.effective_due_amount for f in flist), Decimal("0"))
        paid = sum((fee_amount_paid(f) for f in flist), Decimal("0"))
        pending = sum((fee_balance(f) for f in flist), Decimal("0"))
        rows.append(
            {
                "student": stu,
                "gross": gross,
                "concession": concession,
                "net_due": net_due,
                "paid": paid,
                "pending": pending,
                "fee_lines": len(flist),
            }
        )
    return rows


def build_fee_collect_bundle(student: Student, academic_year):
    """
    Ledger rows, payment targets (lines with balance), history, and totals
    for the billing collect / record-payment UI.
    """
    assign_missing_fees_for_student(student, academic_year)
    qs = (
        Fee.objects.filter(student=student)
        .select_related("fee_structure__fee_type", "academic_year")
        .prefetch_related("payments")
        .order_by("due_date", "fee_structure__fee_type__name", "id")
    )
    if academic_year is not None:
        qs = qs.filter(academic_year=academic_year)
    fee_list = list(qs)
    ledger_rows = []
    payment_targets = []
    total_original = Decimal("0")
    total_discount = Decimal("0")
    total_final = Decimal("0")
    total_paid_sum = Decimal("0")
    total_balance_sum = Decimal("0")
    for f in fee_list:
        orig = f.amount or Decimal("0")
        disc = f.total_concession_amount
        final = f.effective_due_amount
        paid = fee_amount_paid(f)
        bal = fee_balance(f)
        total_original += orig
        total_discount += disc
        total_final += final
        total_paid_sum += paid
        total_balance_sum += bal
        ledger_rows.append(
            {
                "fee": f,
                "original": orig,
                "discount": disc,
                "final_due": final,
                "paid": paid,
                "balance": bal,
                "status": fee_ui_status(f),
            }
        )
        if bal > 0:
            payment_targets.append(
                {
                    "id": f.id,
                    "label": f"{f.fee_structure.fee_type.name} · due {f.due_date}",
                    "balance": bal,
                }
            )
    hist_q = Payment.objects.filter(fee__student=student).select_related(
        "fee__fee_structure__fee_type", "received_by"
    )
    if academic_year is not None:
        hist_q = hist_q.filter(fee__academic_year=academic_year)
    payment_history = list(hist_q.order_by("-payment_date", "-id")[:80])
    return {
        "ledger_rows": ledger_rows,
        "payment_targets": payment_targets,
        "payment_history": payment_history,
        "total_original": total_original,
        "total_discount": total_discount,
        "total_final": total_final,
        "total_paid_sum": total_paid_sum,
        "total_balance_sum": total_balance_sum,
        "scope_fee": fee_list[0] if fee_list else None,
    }


def refresh_fee_status_from_payments(fee: Fee) -> None:
    """Recompute PENDING / PARTIAL / PAID after concession or payment change."""
    fee.refresh_from_db()
    paid = fee_amount_paid(fee)
    eff = fee.effective_due_amount
    if eff <= Decimal("0"):
        fee.status = "PAID"
    elif paid >= eff:
        fee.status = "PAID"
    elif paid > 0:
        fee.status = "PARTIAL"
    else:
        fee.status = "PENDING"
    fee.save(update_fields=["status"])
