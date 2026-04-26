from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django import forms
from django.http import HttpResponseForbidden, JsonResponse
from django.db.models import Q
from django.db.utils import OperationalError, ProgrammingError
from django.core.paginator import Paginator
from django.contrib import messages
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from apps.accounts.decorators import admin_required, student_required
from apps.school_data.classroom_ordering import ORDER_GRADE_NAME
from apps.school_data.models import Student, ClassRoom, Section, Parent, StudentParent
from .models import (
    NotificationLog,
    NotificationChannel,
    NotificationTargetType,
    StudentNotificationRead,
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
    if not has_feature_access(school, "sms", user=request.user):
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

