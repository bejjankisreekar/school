from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect, render
from django.urls import reverse


def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("core:home")


def login_view(request, login_type: str = "portal"):
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

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
            else:
                target = reverse("core:student_dashboard")

            messages.success(request, "Welcome back!")
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
