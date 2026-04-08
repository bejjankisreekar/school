from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.sessions.models import Session
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import (
    SchoolInstitutionProfileForm,
    UserAccountCoreForm,
    UserAvatarForm,
    UserProfileContactForm,
    UserProfileOrganizationForm,
    UserProfilePersonalForm,
    UserProfilePreferencesForm,
    UserProfileSecurityPrefsForm,
)
from .models import User, UserProfile


def _dashboard_redirect_for_user(user):
    role = getattr(user, "role", None)
    if role == "SUPERADMIN":
        return redirect("core:super_admin_dashboard")
    if role == "ADMIN":
        return redirect("core:admin_dashboard")
    if role == "TEACHER":
        return redirect("core:teacher_dashboard")
    if role == "PARENT":
        return redirect("core:parent_dashboard")
    return redirect("core:student_dashboard")


def _profile_breadcrumb_home(user):
    role = getattr(user, "role", None)
    if role == "SUPERADMIN":
        return reverse("core:super_admin_dashboard"), "Dashboard"
    if role == "ADMIN":
        return reverse("core:admin_dashboard"), "Dashboard"
    if role == "TEACHER":
        return reverse("core:teacher_dashboard"), "Dashboard"
    if role == "PARENT":
        return reverse("core:parent_dashboard"), "Dashboard"
    return reverse("core:student_dashboard"), "Dashboard"


def _touch_password_changed(user) -> None:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.password_changed_at = timezone.now()
    profile.save(update_fields=["password_changed_at", "profile_updated_at"])


def _sessions_for_user(user, current_session_key: str | None):
    uid = str(user.pk)
    rows = []
    for s in Session.objects.filter(expire_date__gte=timezone.now()).order_by("-expire_date"):
        try:
            data = s.get_decoded()
        except Exception:
            continue
        if data.get("_auth_user_id") != uid:
            continue
        rows.append(
            {
                "session_key_short": f"{s.session_key[:10]}…",
                "expires": s.expire_date,
                "is_current": s.session_key == current_session_key,
            }
        )
    return rows


def _logout_other_sessions(user, keep_session_key: str | None) -> int:
    uid = str(user.pk)
    deleted = 0
    for s in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
        if keep_session_key and s.session_key == keep_session_key:
            continue
        try:
            data = s.get_decoded()
        except Exception:
            continue
        if data.get("_auth_user_id") == uid:
            s.delete()
            deleted += 1
    return deleted


@login_required
def account_profile(request):
    """Full account profile, preferences, and security overview."""
    user = request.user
    profile, _ = UserProfile.objects.get_or_create(user=user)

    profile_school = getattr(user, "school", None)
    can_edit_school = bool(
        profile_school and getattr(user, "role", None) == User.Roles.ADMIN
    )
    school_form = None
    school_save_failed = False

    core_form = UserAccountCoreForm(instance=user)
    personal_form = UserProfilePersonalForm(instance=profile)
    contact_form = UserProfileContactForm(instance=profile)
    org_form = UserProfileOrganizationForm(instance=profile, user=user)
    prefs_form = UserProfilePreferencesForm(instance=profile)
    security_form = UserProfileSecurityPrefsForm(instance=profile)
    avatar_form = UserAvatarForm(instance=profile)

    profile_save_failed = False
    prefs_save_failed = False
    security_save_failed = False
    avatar_save_failed = False

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "save_profile":
            core_form = UserAccountCoreForm(request.POST, instance=user)
            personal_form = UserProfilePersonalForm(request.POST, instance=profile)
            contact_form = UserProfileContactForm(request.POST, instance=profile)
            org_form = UserProfileOrganizationForm(request.POST, instance=profile, user=user)
            if (
                core_form.is_valid()
                and personal_form.is_valid()
                and contact_form.is_valid()
                and org_form.is_valid()
            ):
                core_form.save()
                personal_form.save()
                contact_form.save()
                org_form.save()
                profile.profile_updated_by = user
                profile.save()
                messages.success(request, "Profile updated successfully.")
                return redirect("accounts:account_profile")
            profile_save_failed = True

        elif action == "save_preferences":
            prefs_form = UserProfilePreferencesForm(request.POST, instance=profile)
            if prefs_form.is_valid():
                prefs_form.save()
                messages.success(request, "Preferences saved.")
                return redirect("accounts:account_profile")
            prefs_save_failed = True

        elif action == "save_security":
            security_form = UserProfileSecurityPrefsForm(request.POST, instance=profile)
            if security_form.is_valid():
                security_form.save()
                messages.success(request, "Security settings updated.")
                return redirect("accounts:account_profile")
            security_save_failed = True

        elif action == "upload_avatar":
            avatar_form = UserAvatarForm(request.POST, request.FILES, instance=profile)
            if avatar_form.is_valid():
                inst = avatar_form.save(commit=False)
                inst.profile_updated_by = user
                inst.save()
                messages.success(request, "Profile photo updated.")
                return redirect("accounts:account_profile")
            avatar_save_failed = True

        elif action == "logout_all_sessions":
            deleted = _logout_other_sessions(user, request.session.session_key)
            messages.success(
                request,
                f"Signed out of {deleted} other session(s). This device stays signed in.",
            )
            return redirect("accounts:account_profile")

        elif action == "save_school":
            if not can_edit_school:
                messages.error(request, "You do not have permission to update institution details.")
                return redirect("accounts:account_profile")
            school_form = SchoolInstitutionProfileForm(request.POST, instance=user.school)
            if school_form.is_valid():
                school_form.save()
                messages.success(request, "Institution profile updated.")
                return redirect("accounts:account_profile")
            school_save_failed = True

    if can_edit_school and school_form is None:
        school_form = SchoolInstitutionProfileForm(instance=profile_school)

    dash_url, dash_label = _profile_breadcrumb_home(user)
    sessions = _sessions_for_user(user, request.session.session_key)

    return render(
        request,
        "accounts/account_profile.html",
        {
            "account_user": user,
            "profile": profile,
            "core_form": core_form,
            "personal_form": personal_form,
            "contact_form": contact_form,
            "org_form": org_form,
            "prefs_form": prefs_form,
            "security_form": security_form,
            "avatar_form": avatar_form,
            "profile_dashboard_url": dash_url,
            "profile_dashboard_label": dash_label,
            "sessions": sessions,
            "other_session_count": sum(1 for s in sessions if not s["is_current"]),
            "profile_save_failed": profile_save_failed,
            "prefs_save_failed": prefs_save_failed,
            "security_save_failed": security_save_failed,
            "avatar_save_failed": avatar_save_failed,
            "profile_school": profile_school,
            "can_edit_school": can_edit_school,
            "school_form": school_form,
            "school_save_failed": school_save_failed,
        },
    )


@login_required
def change_password(request):
    """Voluntary password change while logged in (not first-login flow)."""
    if getattr(request.user, "is_first_login", False):
        return redirect("accounts:change_password_first")

    form = PasswordChangeForm(user=request.user, data=request.POST or None)
    for _fname, field in form.fields.items():
        field.widget.attrs.setdefault("class", "form-control rounded-3")

    if request.method == "POST" and form.is_valid():
        form.save()
        update_session_auth_hash(request, form.user)
        _touch_password_changed(form.user)
        messages.success(request, "Your password was updated successfully.")
        return _dashboard_redirect_for_user(request.user)

    dash_url, dash_label = _profile_breadcrumb_home(request.user)
    return render(
        request,
        "accounts/change_password.html",
        {
            "form": form,
            "settings_dashboard_url": dash_url,
            "settings_dashboard_label": dash_label,
        },
    )


@login_required
def account_settings(request):
    """Settings landing page for all roles."""
    dash_url, dash_label = _profile_breadcrumb_home(request.user)
    return render(
        request,
        "accounts/account_settings.html",
        {
            "settings_dashboard_url": dash_url,
            "settings_dashboard_label": dash_label,
        },
    )


def logout_view(request):
    logout(request)
    return redirect("core:home")


@login_required
def change_password_first(request):
    """Force password change on first login."""
    user = request.user
    if not getattr(user, "is_first_login", False):
        # Already changed; redirect to dashboard
        role = getattr(user, "role", None)
        if role == "SUPERADMIN":
            return redirect("core:super_admin_dashboard")
        if role == "ADMIN":
            return redirect("core:admin_dashboard")
        if role == "TEACHER":
            return redirect("core:teacher_dashboard")
        if role == "PARENT":
            return redirect("core:parent_dashboard")
        return redirect("core:student_dashboard")

    form = PasswordChangeForm(user=user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        update_session_auth_hash(request, form.user)
        _touch_password_changed(user)
        user.is_first_login = False
        user.save(update_fields=["is_first_login"])

        role = getattr(user, "role", None)
        if role == "SUPERADMIN":
            return redirect("core:super_admin_dashboard")
        if role == "ADMIN":
            return redirect("core:admin_dashboard")
        if role == "TEACHER":
            return redirect("core:teacher_dashboard")
        if role == "PARENT":
            return redirect("core:parent_dashboard")
        return redirect("core:student_dashboard")

    return render(request, "accounts/change_password_first.html", {"form": form})


def login_view(request, login_type: str = "portal"):
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            # Clear setup-warning flags so fresh messages can appear if needed
            for key in ("invalid_setup_shown", "fee_not_available_shown"):
                request.session.pop(key, None)

            remember = request.POST.get("remember_me")
            if not remember:
                request.session.set_expiry(0)

            # First-login: force password change before accessing dashboard
            if getattr(user, "is_first_login", False):
                return redirect("accounts:change_password_first")

            next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)

            role = getattr(user, "role", None)
            if role == "SUPERADMIN":
                target = reverse("core:super_admin_dashboard")  # /superadmin/dashboard/
            elif role == "ADMIN":
                target = reverse("core:admin_dashboard")       # /school/dashboard/
            elif role == "TEACHER":
                target = reverse("core:teacher_dashboard")     # /teacher/dashboard/
            elif role == "STUDENT":
                target = reverse("core:student_dashboard")     # /student/dashboard/
            elif role == "PARENT":
                target = reverse("core:parent_dashboard")      # /parent/dashboard/
            else:
                target = reverse("core:student_dashboard")

            return redirect(target)
    else:
        form = AuthenticationForm(request)

    return render(
        request,
        "accounts/login.html",
        {
            "form": form,
            "login_type": login_type,
            "next": request.GET.get("next", "") or "",
        },
    )
