import base64
from io import BytesIO
from datetime import datetime, time, date
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.db import transaction
from django.db.models import Q
from django.urls import reverse

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
    classrooms = ClassRoom.objects.order_by("academic_year", "name")
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
            return redirect("timetable:school_timeslots")
    else:
        from .forms import TimeSlotForm
        form = TimeSlotForm(initial={"order": slots.count()})

    return render(request, "timetable/school_timeslots.html", {
        "slots": slots,
        "form": form,
    })


@admin_required
def school_timeslot_update(request, slot_id):
    """Update a time slot. Expects POST with start_time, end_time, is_break, break_type, order."""
    if not request.user.school:
        return redirect("core:admin_dashboard")
    slot = get_object_or_404(TimeSlot, id=slot_id)
    if request.method != "POST":
        return redirect("timetable:school_timeslots")
    from .forms import TimeSlotForm
    form = TimeSlotForm(request.POST, instance=slot)
    if form.is_valid():
        form.save()
    return redirect("timetable:school_timeslots")


@admin_required
def school_timeslot_delete(request, slot_id):
    """Delete a time slot. Expects POST."""
    if not request.user.school:
        return redirect("core:admin_dashboard")
    slot = get_object_or_404(TimeSlot, id=slot_id)
    if request.method == "POST":
        slot.delete()
    return redirect("timetable:school_timeslots")


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
        return redirect("timetable:school_timetable", classroom_id=classroom.id)

    grid = _build_timetable_grid(classroom, school)

    return render(request, "timetable/school_timetable.html", {
        "classroom": classroom,
        "grid": grid,
        "days": DAYS,
        "subjects": subjects,
        "teachers": teachers,
        "classrooms": list(ClassRoom.objects.exclude(id=classroom.id).order_by("academic_year", "name")),
    })


def _subject_color(subject_name):
    """Return hex color for subject. Uses predefined palette with fallbacks."""
    palette = {
        "mathematics": "#3b82f6", "math": "#3b82f6", "maths": "#3b82f6",
        "physics": "#a855f7", "physical": "#a855f7",
        "english": "#22c55e", "eng": "#22c55e",
        "chemistry": "#f97316", "chem": "#f97316",
        "sports": "#ef4444", "pe": "#ef4444", "physical education": "#ef4444",
        "biology": "#14b8a6", "bio": "#14b8a6",
        "history": "#eab308", "geography": "#ec4899", "geog": "#ec4899",
        "hindi": "#0ea5e9", "sanskrit": "#6366f1",
    }
    if subject_name:
        key = subject_name.lower().strip()
        return palette.get(key, "#e2e8f0")
    return "#e2e8f0"


@admin_required
def school_timetable_print(request, classroom_id):
    """Print-friendly timetable view with school branding, QR, and signatures."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    classroom = get_object_or_404(ClassRoom, id=classroom_id)
    grid = _build_timetable_grid(classroom, school)

    academic_year = classroom.academic_year
    academic_year_name = academic_year.name if academic_year else "—"

    class_teacher = classroom.assigned_teachers.select_related("user").first()
    class_teacher_name = ""
    if class_teacher:
        class_teacher_name = class_teacher.user.get_full_name() or class_teacher.user.username

    first_section = classroom.sections.first()
    section_name = first_section.name if first_section else "—"

    timetable_url = request.build_absolute_uri(
        reverse("timetable:school_timetable", args=[classroom_id])
    )

    qr_data_uri = None
    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=4, border=2)
        qr.add_data(timetable_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        qr_data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        pass

    subject_colors = {}
    for row in grid:
        for d in row["days"]:
            if d.get("entry") and d["entry"].subject:
                subj = d["entry"].subject
                subject_colors[subj.name] = _subject_color(subj.name)

    rows_by_day = []
    for day_val, day_name in DAYS:
        slots_data = []
        for row in grid:
            d = next((x for x in row["days"] if x["day"] == day_val), None)
            entry = d["entry"] if d else None
            bg_color = ""
            if entry and entry.subject:
                bg_color = subject_colors.get(entry.subject.name, "#e2e8f0")
            slots_data.append({"slot": row["slot"], "entry": entry, "bg_color": bg_color})
        rows_by_day.append({"day_name": day_name, "day_val": day_val, "slots": slots_data})

    slots = [row["slot"] for row in grid]

    return render(request, "timetable/timetable_print.html", {
        "classroom": classroom,
        "grid": grid,
        "days": DAYS,
        "slots": slots,
        "school": school,
        "academic_year_name": academic_year_name,
        "section_name": section_name,
        "class_teacher_name": class_teacher_name,
        "printed_date": date.today(),
        "timetable_url": timetable_url,
        "qr_data_uri": qr_data_uri,
        "subject_colors": subject_colors,
        "rows_by_day": rows_by_day,
    })


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
            return redirect("timetable:school_timetable", classroom_id=classroom.id)
        result.seek(0)
        filename = f"timetable-{classroom}-{datetime.now().strftime('%Y%m%d')}.pdf"
        response = HttpResponse(result.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except ImportError:
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
        return redirect("timetable:school_timetable", classroom_id=classroom.id)
    target = ClassRoom.objects.filter(id=target_id).first()
    if not target:
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
