from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


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
