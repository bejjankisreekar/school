from django.urls import path
from . import views

app_name = "core"

urlpatterns = [

    # Student Dashboard
    path("student-dashboard/", views.student_dashboard, name="student_dashboard"),
    path("student-dashboard/profile/", views.student_profile, name="student_profile"),
    path("student-dashboard/attendance/", views.student_attendance, name="student_attendance"),
    path("student-dashboard/marks/", views.student_marks, name="student_marks"),

]