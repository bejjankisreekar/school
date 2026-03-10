from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect, render
from django.urls import reverse


def logout_view(request):
    logout(request)
    return redirect("core:home")


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
        },
    )
