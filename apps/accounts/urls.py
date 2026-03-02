from django.urls import path

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
]

