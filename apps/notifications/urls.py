from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("school/notifications/", views.school_notifications, name="school_notifications"),
    path("student/notifications/", views.student_notifications, name="student_notifications"),
    path("student/notifications/unread-count/", views.student_notifications_unread_count, name="student_notifications_unread_count"),
    path("student/notifications/<int:notification_id>/read/", views.student_notification_mark_read, name="student_notification_mark_read"),
    path("student/chat/", views.student_messages, name="student_messages"),
    path("teacher/chat/", views.teacher_messages, name="teacher_messages"),
    path("school/messages/", views.admin_messages, name="admin_messages"),
    path("api/sections/", views.admin_sections_api, name="admin_sections_api"),
    path("api/students/", views.admin_students_api, name="admin_students_api"),
]

