from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect, render
from django.urls import reverse


def login_view(request):
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            remember = request.POST.get("remember_me")
            if not remember:
                request.session.set_expiry(0)

            role = getattr(user, "role", None)
            if role == "ADMIN":
                target = reverse("core:admin_dashboard")
            elif role == "TEACHER":
                target = reverse("core:teacher_dashboard")
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
        },
    )
