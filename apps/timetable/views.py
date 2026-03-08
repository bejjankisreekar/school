from io import BytesIO
from datetime import datetime, time
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.db import transaction
from django.db.models import Q

from apps.customers.models import School
from apps.school_data.models import ClassRoom, Subject, Teacher
from apps.core.utils import add_warning_once
from apps.accounts.decorators import admin_required, teacher_required, student_required
from .models import TimeSlot, Timetable

DAYS = Timetable.DayOfWeek.choices


def _build_timetable_grid(classroom, school):
    """Build grid of (slot, days) for timetable display."""
    slots = list(TimeSlot.objects.order_by("order", "start_time"))
    existing = {
        (t.day_of_week, t.time_slot_id): t
        for t in Timetable.objects.filter(classroom=classroom)
        .select_related("time_slot", "subject")
        .prefetch_related("teachers__user")
    }
    grid = []
    for slot in slots:
        row = {"slot": slot, "days": []}
        for day_val, day_name in DAYS:
            rec = existing.get((day_val, slot.id))
            row["days"].append({"day": day_val, "day_name": day_name, "entry": rec})
        grid.append(row)
    return grid


def _school_required(view):
    """Ensure user has school; redirect if not."""
    def wrapped(request, *args, **kwargs):
        if not request.user.school:
            add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
            return redirect("core:admin_dashboard")
        return view(request, *args, **kwargs)
    return wrapped


@admin_required
def school_timetable_index(request):
    """List classrooms - pick one to edit timetable."""
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    classrooms = ClassRoom.objects.order_by("name", "section")
    return render(request, "timetable/school_timetable_index.html", {"classrooms": classrooms})


@admin_required
def school_timeslots(request):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")

    slots = TimeSlot.objects.order_by("order", "start_time")

    if request.method == "POST":
        from .forms import TimeSlotForm
        form = TimeSlotForm(request.POST)
        if form.is_valid():
            slot = form.save(commit=False)
            if not slot.order and slots.exists():
                slot.order = slots.order_by("-order").first().order + 1
            slot.save()
            messages.success(request, "Time slot added.")
            return redirect("timetable:school_timeslots")
    else:
        from .forms import TimeSlotForm
        form = TimeSlotForm(initial={"order": slots.count()})

    return render(request, "timetable/school_timeslots.html", {
        "slots": slots,
        "form": form,
    })


@admin_required
def school_timetable(request, classroom_id):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")

    classroom = get_object_or_404(ClassRoom, id=classroom_id)
    slots = list(TimeSlot.objects.order_by("order", "start_time"))
    subjects = Subject.objects.filter(Q(classroom=classroom) | Q(classroom__isnull=True)).order_by("name")
    teachers = Teacher.objects.select_related("user")

    existing = {
        (t.day_of_week, t.time_slot_id): t
        for t in Timetable.objects.filter(classroom=classroom)
        .select_related("time_slot", "subject")
        .prefetch_related("teachers__user")
    }

    if request.method == "POST":
        with transaction.atomic():
            to_create = []
            to_update = []
            desired_teachers = {}  # (day_val, slot.id) -> [teacher_ids...]
            for day_val, _ in DAYS:
                for slot in slots:
                    key = (day_val, slot.id)
                    if slot.is_break:
                        subj_id = None
                        teacher_ids = []
                    else:
                        subj_id = request.POST.get(f"subj_{day_val}_{slot.id}") or None
                        teacher_ids = request.POST.getlist(f"teach_{day_val}_{slot.id}")
                        if subj_id:
                            subj = Subject.objects.filter(id=subj_id).first()
                            if subj and (subj.classroom_id == classroom.id or subj.classroom_id is None):
                                subj_id = int(subj_id)
                            else:
                                subj_id = None
                        # Validate teacher IDs belong to same school
                        teacher_ids = [int(x) for x in teacher_ids if str(x).isdigit()]
                        if teacher_ids:
                            teacher_ids = list(
                                Teacher.objects.filter(id__in=teacher_ids)
                                .values_list("id", flat=True)
                            )

                    if key in existing:
                        rec = existing[key]
                        if rec.subject_id != subj_id:
                            rec.subject_id = subj_id
                            to_update.append(rec)
                    else:
                        to_create.append(Timetable(
                            classroom=classroom,
                            day_of_week=day_val,
                            time_slot=slot,
                            subject_id=subj_id,
                        ))
                    desired_teachers[key] = teacher_ids
            if to_create:
                Timetable.objects.bulk_create(to_create)
            if to_update:
                Timetable.objects.bulk_update(to_update, ["subject_id"])

            # Update teachers M2M via through table (bulk)
            touched = list(existing.values()) + list(to_create)
            touched_ids = [t.id for t in touched if t.id]
            if touched_ids:
                through = Timetable.teachers.through
                through.objects.filter(timetable_id__in=touched_ids).delete()
                m2m_rows = []
                for t in touched:
                    key = (t.day_of_week, t.time_slot_id)
                    for teacher_id in desired_teachers.get(key, []):
                        m2m_rows.append(through(timetable_id=t.id, teacher_id=teacher_id))
                if m2m_rows:
                    through.objects.bulk_create(m2m_rows)
        messages.success(request, "Timetable saved.")
        return redirect("timetable:school_timetable", classroom_id=classroom.id)

    grid = _build_timetable_grid(classroom, school)

    return render(request, "timetable/school_timetable.html", {
        "classroom": classroom,
        "grid": grid,
        "days": DAYS,
        "subjects": subjects,
        "teachers": teachers,
        "classrooms": list(ClassRoom.objects.exclude(id=classroom.id).order_by("name", "section")),
    })


@admin_required
def school_timetable_print(request, classroom_id):
    """Print-friendly timetable view."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    classroom = get_object_or_404(ClassRoom, id=classroom_id)
    grid = _build_timetable_grid(classroom, school)
    return render(request, "timetable/timetable_print.html", {"classroom": classroom, "grid": grid, "days": DAYS})


@admin_required
def school_timetable_pdf(request, classroom_id):
    """Export timetable as PDF."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    classroom = get_object_or_404(ClassRoom, id=classroom_id)
    grid = _build_timetable_grid(classroom, school)
    html = render_to_string("timetable/timetable_pdf.html", {"classroom": classroom, "grid": grid, "days": DAYS})
    try:
        from xhtml2pdf import pisa
        result = BytesIO()
        pisa_status = pisa.CreatePDF(html, dest=result, encoding="utf-8")
        if pisa_status.err:
            messages.error(request, "PDF generation failed.")
            return redirect("timetable:school_timetable", classroom_id=classroom.id)
        result.seek(0)
        filename = f"timetable-{classroom}-{datetime.now().strftime('%Y%m%d')}.pdf"
        response = HttpResponse(result.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except ImportError:
        messages.warning(request, "PDF export requires xhtml2pdf. Use Print → Save as PDF instead.")
        return redirect("timetable:school_timetable_print", classroom_id=classroom.id)


@admin_required
def school_timetable_copy_monday(request, classroom_id):
    """Copy Monday schedule to all weekdays."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    classroom = get_object_or_404(ClassRoom, id=classroom_id)
    monday = Timetable.DayOfWeek.MONDAY
    monday_entries = list(
        Timetable.objects.filter(classroom=classroom, day_of_week=monday)
        .select_related("time_slot", "subject")
        .prefetch_related("teachers__user")
    )
    slots_by_ts = {e.time_slot_id: e for e in monday_entries}
    with transaction.atomic():
        for day_val, _ in Timetable.DayOfWeek.choices:
            if day_val == monday:
                continue
            for ts_id, m_entry in slots_by_ts.items():
                rec, _ = Timetable.objects.update_or_create(
                    classroom=classroom,
                    day_of_week=day_val,
                    time_slot_id=ts_id,
                    defaults={
                        "subject": m_entry.subject,
                        "school": school,
                    },
                )
                rec.teachers.set(list(m_entry.teachers.all()))
    messages.success(request, "Monday schedule copied to all weekdays.")
    return redirect("timetable:school_timetable", classroom_id=classroom.id)


@admin_required
def school_timetable_duplicate(request, classroom_id):
    """Duplicate this timetable to another class. POST: target_classroom_id."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    classroom = get_object_or_404(ClassRoom, id=classroom_id)
    target_id = request.POST.get("target_classroom")
    if not target_id:
        messages.warning(request, "Select a target class.")
        return redirect("timetable:school_timetable", classroom_id=classroom.id)
    target = ClassRoom.objects.filter(id=target_id).first()
    if not target:
        messages.warning(request, "Invalid target class.")
        return redirect("timetable:school_timetable", classroom_id=classroom.id)
    source = list(
        Timetable.objects.filter(classroom=classroom)
        .select_related("time_slot", "subject")
        .prefetch_related("teachers__user")
    )
    with transaction.atomic():
        for e in source:
            rec, _ = Timetable.objects.update_or_create(
                classroom=target,
                day_of_week=e.day_of_week,
                time_slot=e.time_slot,
                defaults={"subject": e.subject, "school": school},
            )
            rec.teachers.set(list(e.teachers.all()))
    messages.success(request, f"Timetable duplicated to {target}.")
    return redirect("timetable:school_timetable", classroom_id=target.id)


@student_required
def student_timetable(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        return render(request, "timetable/student_timetable.html", {"classroom": None, "grid": [], "current_day": None, "current_slot_id": None})

    classroom = student.classroom
    if not classroom:
        return render(request, "timetable/student_timetable.html", {"classroom": None, "grid": [], "current_day": None, "current_slot_id": None})

    school = request.user.school
    slots = list(TimeSlot.objects.order_by("order", "start_time"))
    existing = {
        (t.day_of_week, t.time_slot_id): t
        for t in Timetable.objects.filter(classroom=classroom)
        .select_related("time_slot", "subject")
        .prefetch_related("teachers__user")
    }

    now = datetime.now().time()
    today_weekday = datetime.now().isoweekday()
    current_slot_id = None
    for slot in slots:
        if slot.start_time <= now <= slot.end_time:
            current_slot_id = slot.id
            break

    DAYS = Timetable.DayOfWeek.choices
    grid = []
    for slot in slots:
        row = {"slot": slot, "days": []}
        for day_val, day_name in DAYS:
            rec = existing.get((day_val, slot.id))
            row["days"].append({"day": day_val, "day_name": day_name, "entry": rec})
        grid.append(row)

    return render(request, "timetable/student_timetable.html", {
        "classroom": classroom,
        "grid": grid,
        "current_day": today_weekday,
        "current_slot_id": current_slot_id,
    })


@teacher_required
def teacher_timetable(request):
    teacher = getattr(request.user, "teacher_profile", None)
    if not teacher:
        return render(request, "timetable/teacher_timetable.html", {"entries": []})

    entries = (
        Timetable.objects.filter(teachers=teacher)
        .select_related("time_slot", "subject", "classroom")
        .prefetch_related("teachers__user")
        .order_by("day_of_week", "time_slot__order")
        .distinct()
    )

    return render(request, "timetable/teacher_timetable.html", {
        "entries": entries,
    })


def today_classes_student(student):
    """Get today's classes for a student (for dashboard widget)."""
    if not student or not student.classroom:
        return []
    from datetime import date
    today = date.today().isoweekday()
    return list(
        Timetable.objects.filter(
            classroom=student.classroom,
            day_of_week=today,
            time_slot__is_break=False,
        )
        .select_related("time_slot", "subject")
        .prefetch_related("teachers__user")
        .order_by("time_slot__order")
    )


def today_schedule_teacher(teacher):
    """Get today's schedule for a teacher (for dashboard widget)."""
    if not teacher:
        return []
    from datetime import date
    today = date.today().isoweekday()
    return list(
        Timetable.objects.filter(
            teachers=teacher,
            day_of_week=today,
        )
        .select_related("time_slot", "subject", "classroom")
        .prefetch_related("teachers__user")
        .order_by("time_slot__order")
        .distinct()
    )
