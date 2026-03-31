"""
In-app internal chat: school admin ↔ teacher, teacher ↔ student (same tenant).
"""

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.db.models import OuterRef, Q, Subquery
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import User
from apps.school_data.models import InternalChatMessage, InternalChatThread


def _chat_redirect(request):
    role = getattr(request.user, "role", None)
    if role == User.Roles.TEACHER:
        return redirect("core:teacher_dashboard")
    if role == User.Roles.STUDENT:
        return redirect("core:student_dashboard")
    return redirect("core:admin_dashboard")


def _school_for_chat(request):
    u = request.user
    if not u.is_authenticated or not getattr(u, "school_id", None):
        return None
    if u.role not in (User.Roles.ADMIN, User.Roles.TEACHER, User.Roles.STUDENT):
        return None
    return u.school


def _pair_allowed(a: User, b: User) -> bool:
    if not a.school_id or a.school_id != b.school_id:
        return False
    pair = {a.role, b.role}
    if pair == {User.Roles.ADMIN, User.Roles.TEACHER}:
        return True
    if pair == {User.Roles.TEACHER, User.Roles.STUDENT}:
        return True
    return False


def _ordered_pair(u1: User, u2: User):
    if u1.id < u2.id:
        return u1, u2
    return u2, u1


def _other_user(thread: InternalChatThread, me: User) -> User:
    return thread.user_high if me.id == thread.user_low_id else thread.user_low


def _mark_thread_read(thread: InternalChatThread, reader: User) -> None:
    now = timezone.now()
    if reader.id == thread.user_low_id:
        InternalChatThread.objects.filter(pk=thread.pk).update(user_low_last_read_at=now)
    else:
        InternalChatThread.objects.filter(pk=thread.pk).update(user_high_last_read_at=now)


def _unread_in_thread(thread: InternalChatThread, me: User) -> int:
    last_read = (
        thread.user_low_last_read_at if me.id == thread.user_low_id else thread.user_high_last_read_at
    )
    qs = InternalChatMessage.objects.filter(thread=thread).exclude(sender=me)
    if last_read:
        qs = qs.filter(created_at__gt=last_read)
    return qs.count()


def _eligible_contacts_queryset(me: User, q: str):
    school = me.school
    if me.role == User.Roles.ADMIN:
        qs = User.objects.filter(school=school, role=User.Roles.TEACHER)
    elif me.role == User.Roles.TEACHER:
        qs = User.objects.filter(school=school).filter(
            Q(role=User.Roles.ADMIN) | Q(role=User.Roles.STUDENT)
        )
    else:
        qs = User.objects.filter(school=school, role=User.Roles.TEACHER)
    qs = qs.exclude(pk=me.pk).order_by("first_name", "last_name", "username")
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(username__icontains=q)
            | Q(email__icontains=q)
        )
    return qs


@login_required
@require_http_methods(["GET"])
def internal_chat_inbox(request):
    if not _school_for_chat(request):
        django_messages.warning(request, "Internal chat is not available for your account.")
        return _chat_redirect(request)
    me = request.user
    latest_sub = (
        InternalChatMessage.objects.filter(thread=OuterRef("pk"))
        .order_by("-created_at")
        .values("body")[:1]
    )
    threads = (
        InternalChatThread.objects.filter(Q(user_low=me) | Q(user_high=me))
        .select_related("user_low", "user_high")
        .annotate(last_snippet=Subquery(latest_sub))
        .order_by("-last_message_at")
    )
    for t in threads:
        t.other = _other_user(t, me)
        t.unread_count = _unread_in_thread(t, me)
    return render(
        request,
        "core/internal_chat/inbox.html",
        {
            "threads": threads,
        },
    )


@login_required
@require_http_methods(["GET"])
def internal_chat_contacts(request):
    if not _school_for_chat(request):
        django_messages.warning(request, "Internal chat is not available for your account.")
        return _chat_redirect(request)
    me = request.user
    q = (request.GET.get("q") or "").strip()
    contacts = list(_eligible_contacts_queryset(me, q)[:500])
    for u in contacts:
        if u.role == User.Roles.ADMIN:
            u.role_label = "Admin"
        elif u.role == User.Roles.TEACHER:
            u.role_label = "Teacher"
        else:
            u.role_label = "Student"
    return render(
        request,
        "core/internal_chat/contacts.html",
        {
            "contacts": contacts,
            "q": q,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def internal_chat_thread(request, user_id: int):
    if not _school_for_chat(request):
        django_messages.warning(request, "Internal chat is not available for your account.")
        return _chat_redirect(request)
    me = request.user
    other = get_object_or_404(User.objects.select_related("school"), pk=user_id)
    if other.pk == me.pk:
        return HttpResponseForbidden("Invalid recipient.")
    if not _pair_allowed(me, other):
        return HttpResponseForbidden("You cannot message this user.")

    low, high = _ordered_pair(me, other)
    now = timezone.now()
    thread, _created = InternalChatThread.objects.get_or_create(
        user_low=low,
        user_high=high,
        defaults={"last_message_at": now},
    )

    if request.method == "POST":
        body = (request.POST.get("body") or "").strip()
        if not body:
            django_messages.error(request, "Message cannot be empty.")
            return redirect("core:internal_chat_thread", user_id=user_id)
        if len(body) > 5000:
            django_messages.error(request, "Message is too long.")
            return redirect("core:internal_chat_thread", user_id=user_id)
        InternalChatMessage.objects.create(thread=thread, sender=me, body=body)
        InternalChatThread.objects.filter(pk=thread.pk).update(last_message_at=timezone.now())
        _mark_thread_read(thread, me)
        return redirect("core:internal_chat_thread", user_id=user_id)

    msgs = (
        InternalChatMessage.objects.filter(thread=thread)
        .select_related("sender")
        .order_by("created_at")
    )
    _mark_thread_read(thread, me)
    return render(
        request,
        "core/internal_chat/thread.html",
        {
            "thread": thread,
            "other": other,
            "messages": msgs,
        },
    )


@login_required
@require_GET
def internal_chat_unread_count(request):
    if not _school_for_chat(request):
        return JsonResponse({"unread_count": 0})
    me = request.user
    total = 0
    threads = InternalChatThread.objects.filter(Q(user_low=me) | Q(user_high=me)).only(
        "id",
        "user_low_id",
        "user_high_id",
        "user_low_last_read_at",
        "user_high_last_read_at",
    )
    for t in threads:
        total += _unread_in_thread(t, me)
    return JsonResponse({"unread_count": total})
