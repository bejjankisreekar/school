from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django import forms
from django.http import HttpResponseForbidden, JsonResponse
from django.db import models
from django.db.models import Q
from django.db.utils import OperationalError, ProgrammingError
from django.core.paginator import Paginator
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.views.decorators.http import require_GET
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from apps.accounts.decorators import admin_required, feature_required, student_required, teacher_required
from apps.school_data.classroom_ordering import ORDER_GRADE_NAME
from apps.school_data.models import Student, ClassRoom, Section, Parent
from .chat_permissions import can_user_message, get_allowed_chat_user_ids
from .models import (
    NotificationLog,
    NotificationChannel,
    NotificationTargetType,
    StudentNotificationRead,
    Message,
)
from apps.core.utils import has_feature_access


def _push_realtime_notification(user_id: int, title: str, message: str):
    """Send one realtime websocket event to a specific authenticated user."""
    channel_layer = get_channel_layer()
    if not channel_layer or not user_id:
        return
    async_to_sync(channel_layer.group_send)(
        f"user_{user_id}",
        {
            "type": "send_notification",
            "data": {
                "title": title or "Notification",
                "message": message,
                "event": "notification_created",
            },
        },
    )


class NotificationForm(forms.Form):
    channel = forms.ChoiceField(
        choices=NotificationChannel.choices,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    target_type = forms.ChoiceField(
        choices=NotificationTargetType.choices,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    classroom = forms.ModelChoiceField(
        queryset=ClassRoom.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    section = forms.ModelChoiceField(
        queryset=Section.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    student = forms.ModelChoiceField(
        queryset=Student.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    subject = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm", "placeholder": "Subject (for email)"}),
    )
    body = forms.CharField(
        widget=forms.Textarea(
            attrs={"class": "form-control form-control-sm", "rows": 4, "placeholder": "Message body"}
        )
    )

    def __init__(self, *args, **kwargs):
        school = kwargs.pop("school", None)
        super().__init__(*args, **kwargs)
        if school:
            self.fields["classroom"].queryset = ClassRoom.objects.all().order_by(*ORDER_GRADE_NAME)
            self.fields["section"].queryset = Section.objects.all().order_by("name")
            self.fields["student"].queryset = Student.objects.select_related("user").order_by(
                "user__first_name", "user__last_name"
            )


@admin_required
def school_notifications(request):
    """School Admin: send notification (basic implementation) and view recent logs."""
    school = request.user.school
    if not school:
        return redirect("core:admin_dashboard")
    if not has_feature_access(school, "notifications", user=request.user):
        return HttpResponseForbidden("This feature is not enabled for this school.")

    if request.method == "POST":
        form = NotificationForm(request.POST, school=school)
        if form.is_valid():
            channel = form.cleaned_data["channel"]
            target_type = form.cleaned_data["target_type"]
            classroom = form.cleaned_data.get("classroom")
            section = form.cleaned_data.get("section")
            student = form.cleaned_data.get("student")
            subject = form.cleaned_data.get("subject") or ""
            body = form.cleaned_data["body"]

            recipients = _resolve_recipients(target_type, classroom, section, student)
            logs = []
            for rec in recipients:
                log = NotificationLog.objects.create(
                    school=school,
                    sender=request.user,
                    sender_role=request.user.role,
                    channel=channel,
                    target_type=target_type,
                    target_class=classroom if target_type == NotificationTargetType.CLASS else None,
                    target_section=section if target_type == NotificationTargetType.SECTION else None,
                    target_student=rec.get("student"),
                    recipient_name=rec.get("name", ""),
                    recipient_phone=rec.get("phone", ""),
                    recipient_email=rec.get("email", ""),
                    subject=subject,
                    body=body,
                    status="PENDING",
                )
                logs.append(log)
                recipient_student = rec.get("student")
                if recipient_student and getattr(recipient_student, "user_id", None):
                    _push_realtime_notification(
                        recipient_student.user_id,
                        subject or "New Notification",
                        body,
                    )

            # For now, mark as SENT immediately (no real gateway integration)
            NotificationLog.objects.filter(id__in=[l.id for l in logs]).update(
                status="SENT", sent_at=timezone.now()
            )

            return redirect("core:school_notifications")
    else:
        form = NotificationForm(school=school)

    logs = NotificationLog.objects.filter(school=school).select_related("sender")[:50]
    return render(
        request,
        "notifications/school_notifications.html",
        {
            "form": form,
            "logs": logs,
        },
    )


def _resolve_recipients(target_type, classroom, section, student):
    recipients = []
    if target_type == NotificationTargetType.ALL_STUDENTS:
        qs = Student.objects.select_related("user")
        for s in qs:
            recipients.append(
                {
                    "student": s,
                    "name": s.user.get_full_name() or s.user.username,
                    "phone": getattr(s.user, "phone_number", "") or s.parent_phone,
                    "email": s.user.email,
                }
            )
    elif target_type == NotificationTargetType.ALL_PARENTS:
        for p in Parent.objects.select_related("user"):
            recipients.append(
                {
                    "student": None,
                    "name": p.name,
                    "phone": p.phone,
                    "email": p.email or (p.user.email if p.user else ""),
                }
            )
    elif target_type == NotificationTargetType.CLASS and classroom:
        qs = Student.objects.select_related("user").filter(classroom=classroom)
        for s in qs:
            recipients.append(
                {
                    "student": s,
                    "name": s.user.get_full_name() or s.user.username,
                    "phone": getattr(s.user, "phone_number", "") or s.parent_phone,
                    "email": s.user.email,
                }
            )
    elif target_type == NotificationTargetType.SECTION and section:
        qs = Student.objects.select_related("user").filter(section=section)
        for s in qs:
            recipients.append(
                {
                    "student": s,
                    "name": s.user.get_full_name() or s.user.username,
                    "phone": getattr(s.user, "phone_number", "") or s.parent_phone,
                    "email": s.user.email,
                }
            )
    elif target_type == NotificationTargetType.STUDENT and student:
        s = student
        recipients.append(
            {
                "student": s,
                "name": s.user.get_full_name() or s.user.username,
                "phone": getattr(s.user, "phone_number", "") or s.parent_phone,
                "email": s.user.email,
            }
        )
    return recipients


@student_required
def student_notifications(request):
    """Student notification center (student/class/school-wide notifications)."""
    student = getattr(request.user, "student_profile", None)
    school = getattr(request.user, "school", None)
    if not student or not school:
        return redirect("core:student_dashboard")

    logs_qs = (
        NotificationLog.objects.filter(school=school)
        .filter(
            Q(target_student=student)
            | Q(target_type=NotificationTargetType.CLASS, target_class=student.classroom)
            | Q(target_type=NotificationTargetType.SECTION, target_section=student.section)
            | Q(target_type=NotificationTargetType.ALL_STUDENTS)
        )
        .select_related("sender", "target_class", "target_section", "target_student")
        .order_by("-created_at")
        .distinct()
    )

    try:
        read_ids = set(
            StudentNotificationRead.objects.filter(student=student, notification__in=logs_qs)
            .values_list("notification_id", flat=True)
        )
    except (ProgrammingError, OperationalError):
        # Keep the page usable if schema migration is pending.
        read_ids = set()

    rows = []
    for n in logs_qs:
        rows.append(
            {
                "obj": n,
                "is_read": n.id in read_ids,
            }
        )

    unread_count = sum(1 for r in rows if not r["is_read"])

    paginator = Paginator(rows, 12)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "notifications/student_notifications.html",
        {
            "page_obj": page_obj,
            "unread_count": unread_count,
        },
    )


@student_required
def student_notifications_unread_count(request):
    """Small API for navbar badge bootstrap and fallback refresh."""
    student = getattr(request.user, "student_profile", None)
    school = getattr(request.user, "school", None)
    if not student or not school:
        return JsonResponse({"unread_count": 0}, status=403)

    logs_qs = NotificationLog.objects.filter(school=school).filter(
        Q(target_student=student)
        | Q(target_type=NotificationTargetType.CLASS, target_class=student.classroom)
        | Q(target_type=NotificationTargetType.SECTION, target_section=student.section)
        | Q(target_type=NotificationTargetType.ALL_STUDENTS)
    )
    try:
        read_ids = set(
            StudentNotificationRead.objects.filter(student=student, notification__in=logs_qs)
            .values_list("notification_id", flat=True)
        )
    except (ProgrammingError, OperationalError):
        read_ids = set()
    unread = logs_qs.exclude(id__in=read_ids).distinct().count()
    return JsonResponse({"unread_count": unread})


@student_required
def student_notification_mark_read(request, notification_id):
    """Mark a student notification as read."""
    if request.method != "POST":
        return redirect("notifications:student_notifications")

    student = getattr(request.user, "student_profile", None)
    school = getattr(request.user, "school", None)
    if not student or not school:
        return redirect("core:student_dashboard")

    notification = get_object_or_404(NotificationLog, id=notification_id, school=school)
    allowed = (
        notification.target_student_id == student.id
        or notification.target_type == NotificationTargetType.ALL_STUDENTS
        or (
            notification.target_type == NotificationTargetType.CLASS
            and notification.target_class_id == getattr(student, "classroom_id", None)
        )
        or (
            notification.target_type == NotificationTargetType.SECTION
            and notification.target_section_id == getattr(student, "section_id", None)
        )
    )
    if not allowed:
        return HttpResponseForbidden("You are not allowed to mark this notification.")

    try:
        StudentNotificationRead.objects.get_or_create(student=student, notification=notification)
        messages.success(request, "Notification marked as read.")
    except (ProgrammingError, OperationalError):
        messages.error(
            request,
            "Notifications read-tracking table is not ready yet. Please run migrations.",
        )
    return redirect("notifications:student_notifications")


def _chat_peers_for_user(user):
    peer_ids = get_allowed_chat_user_ids(user)
    if not peer_ids:
        return []
    User = get_user_model()
    users = list(
        User.objects.filter(id__in=peer_ids, is_active=True).order_by("first_name", "last_name", "username")
    )
    unread_map = dict(
        Message.objects.filter(receiver=user, sender_id__in=peer_ids, is_read=False).values_list("sender_id").annotate(
            c=models.Count("id")
        )
    )
    peers = []
    for peer in users:
        peers.append(
            {
                "id": peer.id,
                "name": peer.get_full_name() or peer.username,
                "role": peer.role,
                "unread": int(unread_map.get(peer.id, 0)),
            }
        )
    return peers


def _chat_thread(user, peer_id):
    if not peer_id:
        return []
    thread_qs = Message.objects.filter(
        Q(sender=user, receiver_id=peer_id) | Q(sender_id=peer_id, receiver=user)
    ).select_related("sender", "receiver")
    thread = list(thread_qs.order_by("timestamp", "id"))
    try:
        now = timezone.now()
        Message.objects.filter(sender_id=peer_id, receiver=user, status__in=["sent", "delivered"]).update(
            status="seen",
            seen_at=now,
            is_read=True,
        )
        Message.objects.filter(sender_id=peer_id, receiver=user, is_read=False).update(is_read=True)
    except Exception:
        Message.objects.filter(sender_id=peer_id, receiver=user, is_read=False).update(is_read=True)
    return thread


@student_required
def student_messages(request):
    active_peer_id = request.GET.get("peer")
    peers = _chat_peers_for_user(request.user)
    if request.method == "POST":
        User = get_user_model()
        receiver_id = (request.POST.get("receiver_id") or "").strip()
        content = (request.POST.get("content") or "").strip()
        receiver = User.objects.filter(id=receiver_id).first() if receiver_id else None
        if receiver and content and can_user_message(request.user, receiver):
            Message.objects.create(
                school=request.user.school,
                sender=request.user,
                receiver=receiver,
                content=content,
            )
            return redirect(f"{request.path}?peer={receiver.id}")
        messages.error(request, "Invalid receiver or message content.")
        return redirect(request.path)
    thread = _chat_thread(request.user, int(active_peer_id)) if active_peer_id and active_peer_id.isdigit() else []
    return render(
        request,
        "notifications/student_messages.html",
        {
            "peers": peers,
            "active_peer_id": int(active_peer_id) if active_peer_id and active_peer_id.isdigit() else None,
            "thread": thread,
        },
    )


@teacher_required
def teacher_messages(request):
    peers = _chat_peers_for_user(request.user)
    active_peer_id = request.GET.get("peer")
    if request.method == "POST":
        User = get_user_model()
        receiver_id = (request.POST.get("receiver_id") or "").strip()
        content = (request.POST.get("content") or "").strip()
        receiver = User.objects.filter(id=receiver_id).first() if receiver_id else None
        if receiver and content and can_user_message(request.user, receiver):
            Message.objects.create(
                school=request.user.school,
                sender=request.user,
                receiver=receiver,
                content=content,
            )
            return redirect(f"{request.path}?peer={receiver.id}")
        messages.error(request, "Invalid receiver or message content.")
        return redirect(request.path)
    thread = _chat_thread(request.user, int(active_peer_id)) if active_peer_id and active_peer_id.isdigit() else []
    return render(
        request,
        "notifications/teacher_messages.html",
        {
            "peers": peers,
            "active_peer_id": int(active_peer_id) if active_peer_id and active_peer_id.isdigit() else None,
            "thread": thread,
        },
    )


@admin_required
@feature_required("platform_messaging")
def admin_messages(request):
    User = get_user_model()
    school = getattr(request.user, "school", None)
    active_peer_id = (request.GET.get("peer") or "").strip()
    if request.method == "POST":
        mode = (request.POST.get("mode") or "").strip().lower()
        content = (request.POST.get("content") or "").strip()

        if mode == "broadcast":
            scope = (request.POST.get("broadcast_scope") or "school").strip().lower()
            class_id = (request.POST.get("class_id") or "").strip()
            section_id = (request.POST.get("section_id") or "").strip()

            if not content:
                messages.error(request, "Please type a broadcast message.")
                return redirect(request.path)

            students_qs = Student.objects.select_related("user")
            if scope == "class":
                if not class_id.isdigit():
                    messages.error(request, "Please select a class for class broadcast.")
                    return redirect(request.path)
                students_qs = students_qs.filter(classroom_id=int(class_id))
            elif scope == "section":
                if not class_id.isdigit() or not section_id.isdigit():
                    messages.error(request, "Please select class and section for section broadcast.")
                    return redirect(request.path)
                students_qs = students_qs.filter(classroom_id=int(class_id), section_id=int(section_id))
            else:
                # scope == "school": no extra filters
                pass

            sent = 0
            for s in students_qs.only("id", "user_id"):
                u = getattr(s, "user", None)
                if not u:
                    continue
                if not can_user_message(request.user, u):
                    continue
                Message.objects.create(
                    school=request.user.school,
                    sender=request.user,
                    receiver=u,
                    content=content,
                )
                sent += 1

            if sent:
                messages.success(request, f"Broadcast sent to {sent} students.")
            else:
                messages.warning(request, "No matching students found for broadcast.")
            return redirect(request.path)

        # Default: normal 1:1 message
        receiver_id = (request.POST.get("receiver_id") or "").strip()
        receiver = User.objects.filter(id=receiver_id).first() if receiver_id else None
        if receiver and content and can_user_message(request.user, receiver):
            Message.objects.create(
                school=request.user.school,
                sender=request.user,
                receiver=receiver,
                content=content,
            )
            return redirect(f"{request.path}?peer={receiver.id}")

        messages.error(request, "Invalid receiver or message content.")
        return redirect(request.path)

    # Recent conversations for left panel
    peer_ids = get_allowed_chat_user_ids(request.user) or []
    base_users = User.objects.filter(is_active=True)
    if school:
        base_users = base_users.filter(school=school)

    allowed_users = list(
        base_users.filter(id__in=list(peer_ids)).only("id", "first_name", "last_name", "username", "role")
    )
    allowed_by_id = {u.id: u for u in allowed_users}

    unread_map = {
        uid: c
        for uid, c in Message.objects.filter(receiver=request.user, sender_id__in=list(peer_ids), is_read=False)
        .values_list("sender_id")
        .annotate(c=models.Count("id"))
    }

    last_map = {}  # peer_id -> {ts, preview}
    msg_qs = Message.objects.filter(
        (Q(sender=request.user) & Q(receiver_id__in=list(peer_ids)))
        | (Q(receiver=request.user) & Q(sender_id__in=list(peer_ids)))
    ).only("id", "timestamp", "content", "sender_id", "receiver_id")
    if school:
        msg_qs = msg_qs.filter(school=school)
    for m in msg_qs.order_by("-timestamp", "-id")[:1200]:
        other_id = m.receiver_id if m.sender_id == request.user.id else m.sender_id
        if other_id in last_map:
            continue
        prev = (m.content or "").strip().replace("\n", " ")
        last_map[other_id] = {"ts": m.timestamp, "preview": (prev[:120] + "…") if len(prev) > 120 else prev}

    peers = []
    for uid in peer_ids:
        u = allowed_by_id.get(uid)
        if not u:
            continue
        last = last_map.get(uid) or {}
        peers.append(
            {
                "id": u.id,
                "name": u.get_full_name() or u.username,
                "role": getattr(u, "role", ""),
                "unread": int(unread_map.get(u.id, 0)),
                "last_ts": last.get("ts"),
                "last_preview": last.get("preview", ""),
            }
        )
    peers.sort(key=lambda x: (x["last_ts"] is not None, x["last_ts"] or timezone.now()), reverse=True)

    show_all = (request.GET.get("all") or "").strip() == "1"

    student_peers = [p for p in peers if p.get("role") == User.Roles.STUDENT]
    teacher_peers = [p for p in peers if p.get("role") == User.Roles.TEACHER]
    other_peers = [p for p in peers if p.get("role") not in (User.Roles.STUDENT, User.Roles.TEACHER)]
    # Keep other roles visible under Teachers section (parents/admin/etc.).
    teacher_peers = teacher_peers + other_peers

    if not show_all:
        student_peers = student_peers[:6]
        teacher_peers = teacher_peers[:6]

    active_peer = None
    if active_peer_id.isdigit():
        active_peer = base_users.filter(id=int(active_peer_id)).first()

    thread = _chat_thread(request.user, int(active_peer_id)) if active_peer else []

    classes = list(ClassRoom.objects.order_by("grade_order", "name").only("id", "name"))
    return render(
        request,
        "notifications/admin_messages.html",
        {
            "student_peers": student_peers,
            "teacher_peers": teacher_peers,
            "active_peer_id": int(active_peer_id) if active_peer else None,
            "active_peer": active_peer,
            "thread": thread,
            "classes": classes,
            "show_all": show_all,
        },
    )


@admin_required
@feature_required("platform_messaging")
@require_GET
def admin_sections_api(request):
    """Sections dropdown for school admin internal chat; same plan gate as ``/school/messages/``."""
    class_id = (request.GET.get("class_id") or "").strip()
    if not class_id.isdigit():
        return JsonResponse({"results": []})
    classroom = ClassRoom.objects.filter(id=int(class_id)).prefetch_related("sections").first()
    if not classroom:
        return JsonResponse({"results": []})
    secs = list(classroom.sections.order_by("name").values("id", "name"))
    return JsonResponse({"results": secs})


@admin_required
@feature_required("platform_messaging")
@require_GET
def admin_students_api(request):
    """Student list for school admin internal chat; same plan gate as ``/school/messages/``."""
    class_id = (request.GET.get("class_id") or "").strip()
    section_id = (request.GET.get("section_id") or "").strip()

    qs = Student.objects.select_related("user", "classroom", "section")
    if class_id.isdigit():
        qs = qs.filter(classroom_id=int(class_id))
    if section_id.isdigit():
        qs = qs.filter(section_id=int(section_id))

    # Require at least one filter to avoid returning the whole school by accident.
    if not class_id.isdigit() and not section_id.isdigit():
        return JsonResponse({"results": []})

    qs = qs.order_by("user__first_name", "user__username")[:300]

    results = []
    for s in qs:
        u = getattr(s, "user", None)
        name = (u.get_full_name() if u else "") or (getattr(u, "username", "") if u else "") or "Student"
        classroom_name = getattr(getattr(s, "classroom", None), "name", "") or "—"
        section_name = getattr(getattr(s, "section", None), "name", "") or "—"
        roll = getattr(s, "roll_number", "") or "—"
        hay = f"{name} {classroom_name} {section_name} {roll} {getattr(u, 'username', '') if u else ''}".lower()
        results.append(
            {
                "id": s.id,
                "user_id": getattr(u, "id", None),
                "name": name,
                "classroom": classroom_name,
                "section": section_name,
                "roll_number": roll,
                "hay": hay,
            }
        )
    return JsonResponse({"results": results})

