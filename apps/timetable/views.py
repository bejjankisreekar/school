import base64
from io import BytesIO
from datetime import datetime, time, date
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, HttpResponseForbidden
from django.template.loader import render_to_string
from django.db import transaction
from django.db.models import Case, IntegerField, Max, Q, Value, When
from django.urls import reverse

from apps.customers.models import School
from apps.school_data.models import ClassRoom, Subject, Teacher
from apps.core.tenant_scope import ensure_tenant_for_request
from apps.core.utils import add_warning_once, has_feature_access
from apps.accounts.decorators import admin_required, teacher_required, student_required
from .forms import ScheduleProfileForm, TimeSlotAddForm, TimeSlotForm
from .models import ScheduleProfile, TimeSlot, Timetable

DAYS = Timetable.DayOfWeek.choices


def _default_schedule_profile():
    """Single canonical default profile per tenant (schema)."""
    return ScheduleProfile.objects.get_or_create(name="Default")[0]


def _current_published_profile_for_school(school, default_profile):
    """
    One published timetable profile for the whole school.
    Stored in public schema as a JSON setting on School to avoid tenant migration churn.
    """
    raw = getattr(school, "timetable_current_profile_id", None)
    try:
        pid = int(raw) if raw else None
    except (TypeError, ValueError):
        pid = None
    if not pid:
        return default_profile
    return ScheduleProfile.objects.filter(pk=pid).first() or default_profile


def _timeslot_qs_for_profile(profile, default_profile):
    """
    Time slots for this schedule profile.

    Legacy rows (pre–ScheduleProfile) have profile_id NULL; those belong to the
    Default profile only so existing tenant data in timetable_timeslot keeps working.
    """
    qs = TimeSlot.objects.all()
    if profile.pk == default_profile.pk:
        qs = qs.filter(Q(profile_id=profile.pk) | Q(profile__isnull=True))
    else:
        qs = qs.filter(profile=profile)
    return qs.order_by("order", "start_time")


def _timetable_qs_for_classroom_profile(classroom, profile, default_profile):
    """Timetable entries for this class + profile, including legacy NULL profile rows for Default."""
    qs = Timetable.objects.filter(classroom=classroom)
    if profile.pk == default_profile.pk:
        qs = qs.filter(Q(profile_id=profile.pk) | Q(profile__isnull=True))
    else:
        qs = qs.filter(profile=profile)
    return qs


def _timetable_existing_dict(classroom, profile, default_profile):
    """
    Map (day_of_week, time_slot_id) -> Timetable row.

    When duplicate rows exist (e.g. legacy profile NULL + explicit Default), prefer the row
    whose profile matches the active profile so saved data shows reliably.
    """
    qs = (
        _timetable_qs_for_classroom_profile(classroom, profile, default_profile)
        .select_related("time_slot", "subject")
        .prefetch_related("teachers__user")
        .annotate(
            _tpref=Case(
                When(profile_id=profile.pk, then=Value(0)),
                When(profile__isnull=True, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        .order_by("_tpref", "id")
    )
    out = {}
    for t in qs:
        key = (t.day_of_week, t.time_slot_id)
        if key not in out:
            out[key] = t
    return out


def _slot_allowed_for_profile(slot, profile_id: int, default_profile) -> bool:
    """Whether a time slot may be edited/deleted from the given profile context."""
    if slot.profile_id == profile_id:
        return True
    return profile_id == default_profile.pk and slot.profile_id is None


def _normalize_timeslot_orders(profile, default_profile):
    """Assign order = 1..n by current sequence (order, start_time, id)."""
    ordered = list(
        _timeslot_qs_for_profile(profile, default_profile).order_by("order", "start_time", "id")
    )
    for i, slot in enumerate(ordered, start=1):
        if slot.order != i:
            TimeSlot.objects.filter(pk=slot.pk).update(order=i)


def _build_timetable_grid(classroom, school, profile=None):
    """Build grid of (slot, days) for schedule display."""
    default_profile = _default_schedule_profile()
    profile = profile or getattr(classroom, "active_schedule_profile", None) or default_profile

    slots = list(_timeslot_qs_for_profile(profile, default_profile))
    existing = _timetable_existing_dict(classroom, profile, default_profile)
    grid = []
    for slot in slots:
        row = {"slot": slot, "days": []}
        for day_val, day_name in DAYS:
            rec = existing.get((day_val, slot.id))
            subject_bg = None
            if rec and rec.subject_id:
                subject_bg = _subject_color(rec.subject.name)
            row["days"].append(
                {
                    "day": day_val,
                    "day_name": day_name,
                    "entry": rec,
                    "subject_bg": subject_bg,
                }
            )
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
    if not has_feature_access(school, "timetable", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    ensure_tenant_for_request(request)
    import re

    def _grade_sort_key(c: ClassRoom):
        """
        Prefer higher grades first (e.g. Grade 10 before Grade 9).
        Falls back to name ordering when no number is found.
        """
        name = (getattr(c, "name", "") or "").strip()
        m = re.search(r"(\d+)", name)
        grade_num = int(m.group(1)) if m else -1
        # Put "current" academic years first when present.
        ay_start = getattr(getattr(c, "academic_year", None), "start_date", None)
        ay_key = ay_start or date.min
        return (-ay_key.toordinal(), -grade_num, name.lower(), c.id)

    default_profile = _default_schedule_profile()
    classrooms = list(
        ClassRoom.objects.select_related("academic_year", "active_schedule_profile").all()
    )
    classrooms.sort(key=_grade_sort_key)
    return render(
        request,
        "timetable/school_timetable_index.html",
        {"classrooms": classrooms, "default_profile": default_profile},
    )


@admin_required
def school_timeslots(request):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    if not has_feature_access(school, "timetable", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    ensure_tenant_for_request(request)

    default_profile = _default_schedule_profile()
    profile_id = request.GET.get("profile") or request.POST.get("profile")
    selected_profile = default_profile
    if profile_id:
        try:
            selected_profile = ScheduleProfile.objects.select_related("academic_year").get(
                id=int(profile_id)
            )
        except (ValueError, ScheduleProfile.DoesNotExist):
            selected_profile = default_profile

    profiles = ScheduleProfile.objects.all()
    slots = _timeslot_qs_for_profile(selected_profile, default_profile)

    show_new_profile_modal = False
    new_profile_form = ScheduleProfileForm()

    current_profile = _current_published_profile_for_school(school, default_profile)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_schedule_profile":
            new_profile_form = ScheduleProfileForm(request.POST)
            show_new_profile_modal = not new_profile_form.is_valid()
            if new_profile_form.is_valid():
                created = new_profile_form.save()
                messages.success(
                    request,
                    f'Schedule profile "{created.name}" was created. Add time slots below.',
                )
                return redirect(f"{reverse('timetable:school_timeslots')}?profile={created.id}")
            form = TimeSlotAddForm()
        else:
            if action == "set_current_schedule_profile":
                school.timetable_current_profile_id = selected_profile.id
                school.save(update_fields=["timetable_current_profile_id"])
                messages.success(request, f'Published timetable profile: "{selected_profile.name}".')
                return redirect(f"{reverse('timetable:school_timeslots')}?profile={selected_profile.id}")

            form = TimeSlotAddForm(request.POST)
            if form.is_valid():
                slot = form.save(commit=False)
                slot.profile = selected_profile
                max_o = (
                    _timeslot_qs_for_profile(selected_profile, default_profile).aggregate(
                        m=Max("order")
                    )["m"]
                )
                max_o = max_o if max_o is not None else 0
                slot.order = max_o + 1
                slot.save()
                _normalize_timeslot_orders(selected_profile, default_profile)
                messages.success(request, "Time slot added.")
                return redirect(f"{reverse('timetable:school_timeslots')}?profile={selected_profile.id}")
    else:
        form = TimeSlotAddForm()

    return render(
        request,
        "timetable/school_timeslots.html",
        {
            "slots": slots,
            "form": form,
            "profiles": profiles,
            "selected_profile": selected_profile,
            "current_profile": current_profile,
            "new_profile_form": new_profile_form,
            "show_new_profile_modal": show_new_profile_modal,
        },
    )


@admin_required
def school_schedule_profile_edit(request, profile_id):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    if not has_feature_access(school, "timetable", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    ensure_tenant_for_request(request)

    profile = get_object_or_404(ScheduleProfile, pk=profile_id)
    form = ScheduleProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Schedule profile updated.")
        return redirect(f"{reverse('timetable:school_timeslots')}?profile={profile.id}")

    return render(
        request,
        "timetable/schedule_profile_form.html",
        {"form": form, "profile": profile},
    )


@admin_required
def school_timeslot_update(request, slot_id):
    """Update a time slot. Expects POST with start_time, end_time, is_break, break_type, order."""
    if not request.user.school:
        return redirect("core:admin_dashboard")
    if not has_feature_access(request.user.school, "timetable", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    ensure_tenant_for_request(request)
    if request.method != "POST":
        return redirect("timetable:school_timeslots")
    profile_id = request.POST.get("profile")
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return redirect("timetable:school_timeslots")
    default_profile = _default_schedule_profile()
    slot = get_object_or_404(TimeSlot, id=slot_id)
    if not _slot_allowed_for_profile(slot, pid, default_profile):
        return redirect("timetable:school_timeslots")
    form = TimeSlotForm(request.POST, instance=slot)
    if form.is_valid():
        updated = form.save(commit=False)
        updated.profile_id = default_profile.pk if slot.profile_id is None else slot.profile_id
        updated.save()
        prof = ScheduleProfile.objects.filter(pk=updated.profile_id).first() or default_profile
        _normalize_timeslot_orders(prof, default_profile)
        messages.success(request, "Time slot updated.")
    profile_id = request.POST.get("profile") or request.GET.get("profile")
    if profile_id:
        return redirect(f"{reverse('timetable:school_timeslots')}?profile={profile_id}")
    return redirect("timetable:school_timeslots")


@admin_required
def school_timeslot_delete(request, slot_id):
    """Delete a time slot. Expects POST."""
    if not request.user.school:
        return redirect("core:admin_dashboard")
    if not has_feature_access(request.user.school, "timetable", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    ensure_tenant_for_request(request)
    if request.method != "POST":
        return redirect("timetable:school_timeslots")
    profile_id = request.POST.get("profile")
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return redirect("timetable:school_timeslots")
    default_profile = _default_schedule_profile()
    slot = get_object_or_404(TimeSlot, id=slot_id)
    if not _slot_allowed_for_profile(slot, pid, default_profile):
        return redirect("timetable:school_timeslots")
    prof = (
        default_profile
        if slot.profile_id is None
        else (ScheduleProfile.objects.filter(pk=slot.profile_id).first() or default_profile)
    )
    slot.delete()
    _normalize_timeslot_orders(prof, default_profile)
    messages.success(request, "Time slot removed.")
    profile_id = request.POST.get("profile") or request.GET.get("profile")
    if profile_id:
        return redirect(f"{reverse('timetable:school_timeslots')}?profile={profile_id}")
    return redirect("timetable:school_timeslots")


@admin_required
def school_timetable(request, classroom_id):
    school = request.user.school
    if not school:
        add_warning_once(request, "invalid_setup_shown", "Invalid setup.")
        return redirect("core:admin_dashboard")
    if not has_feature_access(school, "timetable", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    ensure_tenant_for_request(request)

    default_profile = _default_schedule_profile()
    classroom = get_object_or_404(ClassRoom, id=classroom_id)
    profiles = ScheduleProfile.objects.order_by("name")
    active_profile = classroom.active_schedule_profile or default_profile

    if request.method == "POST" and request.POST.get("set_profile"):
        pid = request.POST.get("set_profile")
        try:
            classroom.active_schedule_profile = ScheduleProfile.objects.get(id=int(pid))
            classroom.save(update_fields=["active_schedule_profile"])
        except (ValueError, ScheduleProfile.DoesNotExist):
            pass
        return redirect("timetable:school_timetable", classroom_id=classroom.id)

    slots = list(_timeslot_qs_for_profile(active_profile, default_profile))
    subjects = list(Subject.objects.all().order_by("name"))
    teachers = list(Teacher.objects.select_related("user").order_by("user__last_name", "user__first_name"))

    existing = _timetable_existing_dict(classroom, active_profile, default_profile)

    if request.method == "POST" and request.POST.get("save_timetable"):
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
                            if subj:
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
                            profile=active_profile,
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

    grid = _build_timetable_grid(classroom, school, profile=active_profile)
    edit_mode = request.GET.get("edit") == "1"

    return render(request, "timetable/school_timetable.html", {
        "classroom": classroom,
        "grid": grid,
        "days": DAYS,
        "subjects": subjects,
        "teachers": teachers,
        "profiles": profiles,
        "active_profile": active_profile,
        "edit_mode": edit_mode,
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
    ensure_tenant_for_request(request)
    default_profile = _default_schedule_profile()
    classroom = get_object_or_404(ClassRoom, id=classroom_id)
    active_profile = classroom.active_schedule_profile or default_profile
    grid = _build_timetable_grid(classroom, school, profile=active_profile)

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
        "active_profile": active_profile,
        "grid": grid,
        "days": DAYS,
        "slots": slots,
        "school": school,
        "academic_year_name": academic_year_name,
        "section_name": section_name,
        "class_teacher_name": class_teacher_name,
        "printed_date": timezone.localdate(),
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
    ensure_tenant_for_request(request)
    default_profile = _default_schedule_profile()
    classroom = get_object_or_404(ClassRoom, id=classroom_id)
    active_profile = classroom.active_schedule_profile or default_profile
    grid = _build_timetable_grid(classroom, school, profile=active_profile)
    html = render_to_string(
        "timetable/timetable_pdf.html",
        {"classroom": classroom, "active_profile": active_profile, "grid": grid, "days": DAYS},
    )
    try:
        from xhtml2pdf import pisa
        result = BytesIO()
        pisa_status = pisa.CreatePDF(html, dest=result, encoding="utf-8")
        if pisa_status.err:
            return redirect("timetable:school_timetable", classroom_id=classroom.id)
        result.seek(0)
        filename = f"timetable-{classroom}-{timezone.localdate().strftime('%Y%m%d')}.pdf"
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
    ensure_tenant_for_request(request)
    classroom = get_object_or_404(ClassRoom, id=classroom_id)
    default_profile = _default_schedule_profile()
    active_profile = classroom.active_schedule_profile or default_profile
    monday = Timetable.DayOfWeek.MONDAY
    monday_entries = list(
        _timetable_qs_for_classroom_profile(classroom, active_profile, default_profile)
        .filter(day_of_week=monday)
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
                    profile=active_profile,
                    defaults={"subject": m_entry.subject},
                )
                rec.teachers.set(list(m_entry.teachers.all()))
    return redirect("timetable:school_timetable", classroom_id=classroom.id)


@admin_required
def school_timetable_duplicate(request, classroom_id):
    """Duplicate this timetable to another class. POST: target_classroom_id."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    ensure_tenant_for_request(request)
    classroom = get_object_or_404(ClassRoom, id=classroom_id)
    target_id = request.POST.get("target_classroom")
    if not target_id:
        return redirect("timetable:school_timetable", classroom_id=classroom.id)
    target = ClassRoom.objects.filter(id=target_id).first()
    if not target:
        return redirect("timetable:school_timetable", classroom_id=classroom.id)
    default_profile = _default_schedule_profile()
    active_source = classroom.active_schedule_profile or default_profile
    active_target = target.active_schedule_profile or default_profile
    source = list(
        _timetable_qs_for_classroom_profile(classroom, active_source, default_profile)
        .select_related("time_slot", "subject")
        .prefetch_related("teachers__user")
    )
    with transaction.atomic():
        for e in source:
            rec, _ = Timetable.objects.update_or_create(
                classroom=target,
                day_of_week=e.day_of_week,
                time_slot=e.time_slot,
                profile=active_target,
                defaults={"subject": e.subject},
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
    if not has_feature_access(school, "timetable", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    ensure_tenant_for_request(request)
    default_profile = _default_schedule_profile()
    active = _current_published_profile_for_school(school, default_profile)
    grid = _build_timetable_grid(classroom, school, profile=active)

    return render(
        request,
        "timetable/school_timetable.html",
        {
            "portal_readonly": True,
            "portal_role": "student",
            "classroom": classroom,
            "grid": grid,
            "days": DAYS,
            "active_profile": active,
            "edit_mode": False,
        },
    )


@teacher_required
def teacher_timetable(request):
    """
    Weekly grid: time slots × weekdays — same shape as the class timetable.
    Scheduled teaching appears in cells; empty teaching periods show as leisure.
    """
    teacher = getattr(request.user, "teacher_profile", None)
    if not teacher:
        return render(
            request,
            "timetable/teacher_timetable.html",
            {
                "grid": [],
                "current_day": None,
                "current_slot_id": None,
                "has_slots": False,
                "no_teacher": True,
            },
        )
    school = request.user.school
    if not has_feature_access(school, "timetable", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")
    ensure_tenant_for_request(request)

    default_profile = _default_schedule_profile()
    active = _current_published_profile_for_school(school, default_profile)
    slots = list(_timeslot_qs_for_profile(active, default_profile))
    entries_qs = (
        Timetable.objects.filter(teachers=teacher)
        .filter(
            Q(profile=active)
            | (Q(profile__isnull=True) if active.pk == default_profile.pk else Q(pk__in=[]))
        )
        .select_related("time_slot", "subject", "classroom")
        .prefetch_related("teachers__user")
        .order_by("classroom__name", "subject__name")
    )

    by_cell = {}
    for t in entries_qs:
        key = (t.day_of_week, t.time_slot_id)
        by_cell.setdefault(key, []).append(t)

    now = timezone.localtime(timezone.now()).time()
    today_weekday = timezone.localdate().isoweekday()
    current_slot_id = None
    for slot in slots:
        if slot.start_time <= now <= slot.end_time:
            current_slot_id = slot.id
            break

    grid = []
    for slot in slots:
        row = {"slot": slot, "days": []}
        for day_val, day_name in DAYS:
            cell_entries = by_cell.get((day_val, slot.id), [])
            row["days"].append(
                {
                    "day": day_val,
                    "day_name": day_name,
                    "entries": cell_entries,
                }
            )
        grid.append(row)

    return render(
        request,
        "timetable/school_timetable.html",
        {
            "portal_readonly": True,
            "portal_role": "teacher",
            "grid": grid,
            "classroom": None,
            "days": DAYS,
            "active_profile": active,
            "edit_mode": False,
        },
    )


def today_classes_student(student):
    """Get today's classes for a student (for dashboard widget)."""
    if not student or not student.classroom:
        return []
    from datetime import date

    today = timezone.localdate().isoweekday()
    default_profile = _default_schedule_profile()
    profile = _current_published_profile_for_school(student.user.school, default_profile)
    qs = Timetable.objects.filter(
        classroom=student.classroom,
        day_of_week=today,
        time_slot__is_break=False,
    )
    if profile.pk == default_profile.pk:
        qs = qs.filter(Q(profile_id=profile.pk) | Q(profile__isnull=True))
    else:
        qs = qs.filter(profile=profile)
    return list(
        qs.select_related("time_slot", "subject")
        .prefetch_related("teachers__user")
        .order_by("time_slot__order")
    )


def today_schedule_teacher(teacher):
    """Get today's schedule for a teacher (for dashboard widget)."""
    if not teacher:
        return []
    from datetime import date
    today = timezone.localdate().isoweekday()
    default_profile = _default_schedule_profile()
    profile = _current_published_profile_for_school(teacher.user.school, default_profile)
    return list(
        Timetable.objects.filter(
            teachers=teacher,
            day_of_week=today,
        ).filter(
            Q(profile=profile)
            | (Q(profile__isnull=True) if profile.pk == default_profile.pk else Q(pk__in=[]))
        )
        .select_related("time_slot", "subject", "classroom")
        .prefetch_related("teachers__user")
        .order_by("time_slot__order")
        .distinct()
    )
