from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.school_reports_dashboard, name="dashboard"),
    path("students/analytics/", views.school_report_student_analytics, name="student_analytics"),
    path("students-by-class/", views.school_report_students_by_class, name="students_by_class"),
    path("students-by-class/data/", views.school_report_students_by_class_data, name="students_by_class_data"),
    path("attendance-trend/", views.school_report_attendance_trend, name="attendance_trend"),
    path("toppers/", views.school_reports_toppers, name="toppers"),
]
