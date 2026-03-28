from django.urls import path
from django.contrib.auth import views as auth_views

from . import views


app_name = "accounts"

urlpatterns = [
    # Generic login (defaults to student/teacher portal)
    path("login/", views.login_view, name="login"),
    # School / admin login entry
    path("school-login/", views.login_view, {"login_type": "school"}, name="school_login"),
    # Student / teacher login entry
    path("portal-login/", views.login_view, {"login_type": "portal"}, name="portal_login"),
    # Logout
    path("logout/", views.logout_view, name="logout"),
    # First-login: force change password
    path("change-password/", views.change_password_first, name="change_password_first"),
    # Password reset
    path("password_reset/", auth_views.PasswordResetView.as_view(
        template_name="accounts/password_reset.html",
        email_template_name="accounts/password_reset_email.html",
        subject_template_name="accounts/password_reset_subject.txt",
        success_url="/accounts/password_reset/done/",
    ), name="password_reset"),
    path("password_reset/done/", auth_views.PasswordResetDoneView.as_view(
        template_name="accounts/password_reset_done.html",
    ), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="accounts/password_reset_confirm.html",
        success_url="/accounts/reset/done/",
    ), name="password_reset_confirm"),
    path("reset/done/", auth_views.PasswordResetCompleteView.as_view(
        template_name="accounts/password_reset_complete.html",
    ), name="password_reset_complete"),
]

