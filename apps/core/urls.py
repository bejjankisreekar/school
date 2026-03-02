from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    # Public landing page / home
    path("", views.home, name="home"),

    # Role-based dashboard URLs (for login redirect)
    path("superadmin/dashboard/", views.super_admin_dashboard, name="super_admin_dashboard"),
    path("school/dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("teacher/dashboard/", views.teacher_dashboard, name="teacher_dashboard"),
    path("student/dashboard/", views.student_dashboard, name="student_dashboard"),

    # Legacy/alias dashboard URLs
    path("super-admin/", views.super_admin_dashboard, name="super_admin_dashboard_legacy"),
    path("school-admin/", views.admin_dashboard, name="admin_dashboard_legacy"),
    path("student-dashboard/", views.student_dashboard, name="student_dashboard_legacy"),
    path("student-dashboard/profile/", views.student_profile, name="student_profile"),
    path("student-dashboard/marks/", views.student_marks, name="student_marks"),
    path("student/attendance/", views.student_attendance, name="student_attendance"),
    path("student/exams/", views.student_exams_list, name="student_exams_list"),
    path("student/exam/<str:exam_name>/", views.student_exam_detail, name="student_exam_detail"),

    # Teacher Exam Management
    path("teacher/exams/", views.teacher_exams, name="teacher_exams"),
    path("teacher/class-analytics/", views.teacher_class_analytics, name="teacher_class_analytics"),

    # Sidebar items (unified)
    path("students/", views.students_list, name="students_list"),
    path("teachers/", views.teachers_list, name="teachers_list"),
    path("attendance/", views.attendance_list, name="attendance_list"),
    path("marks/", views.marks_list, name="marks_list"),
    path("homework/", views.homework_list, name="homework_list"),
    path("reports/", views.reports_list, name="reports_list"),

    # Teacher actions
    path("teacher/students/", views.teacher_students_list, name="teacher_students_list"),
    path("teacher/homework/create/", views.create_homework, name="create_homework"),
    path("teacher/marks/enter/", views.enter_marks, name="enter_marks"),
    path("teacher/attendance/", views.bulk_attendance, name="bulk_attendance"),
    path("teacher/attendance/mark/", views.mark_attendance, name="mark_attendance"),
]