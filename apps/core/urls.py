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
    path("student/exam/<int:exam_id>/", views.student_exam_detail_by_id, name="student_exam_detail_by_id"),
    path("student/exam/legacy/<str:exam_name>/", views.student_exam_detail, name="student_exam_detail"),
    path("student/reports/", views.student_reports, name="student_reports"),
    path("student/report-card/<int:exam_id>/", views.student_report_card_pdf, name="student_report_card_pdf"),
    path("student/attendance-report/", views.student_attendance_report_pdf, name="student_attendance_report_pdf"),

    # Teacher Exam Management
    path("teacher/exams/", views.teacher_exams, name="teacher_exams"),
    path("teacher/exams/create/", views.teacher_exam_create, name="teacher_exam_create"),
    path("teacher/exams/<int:exam_id>/", views.teacher_exam_summary, name="teacher_exam_summary"),
    path("teacher/exams/<int:exam_id>/enter-marks/", views.teacher_exam_enter_marks, name="teacher_exam_enter_marks"),
    path("teacher/class-analytics/", views.teacher_class_analytics, name="teacher_class_analytics"),

    # Sidebar items (unified)
    path("students/", views.students_list, name="students_list"),
    path("teachers/", views.teachers_list, name="teachers_list"),
    # School Admin: Student management
    path("school/students/", views.school_students_list, name="school_students_list"),
    path("school/students/add/", views.school_student_add, name="school_student_add"),
    path("school/students/<int:student_id>/edit/", views.school_student_edit, name="school_student_edit"),
    path("school/students/<int:student_id>/delete/", views.school_student_delete, name="school_student_delete"),
    path("school/students/import/", views.school_students_import, name="school_students_import"),
    # School Admin: Teacher management
    path("school/teachers/", views.school_teachers_list, name="school_teachers_list"),
    path("school/teachers/add/", views.school_teacher_add, name="school_teacher_add"),
    path("school/teachers/<int:teacher_id>/edit/", views.school_teacher_edit, name="school_teacher_edit"),
    path("school/teachers/<int:teacher_id>/delete/", views.school_teacher_delete, name="school_teacher_delete"),
    # School Admin: Academic years, classes, sections, subjects
    path("school/academic-years/", views.school_academic_years, name="school_academic_years"),
    path("school/academic-years/<int:year_id>/edit/", views.school_academic_year_edit, name="school_academic_year_edit"),
    path("school/academic-years/<int:year_id>/set-active/", views.school_academic_year_set_active, name="school_academic_year_set_active"),
    path("school/academic-years/<int:year_id>/delete/", views.school_academic_year_delete, name="school_academic_year_delete"),
    path("school/classes/", views.school_classes, name="school_classes"),
    path("school/classes/add/", views.school_class_add, name="school_class_add"),
    path("school/classes/<int:class_id>/edit/", views.school_class_edit, name="school_class_edit"),
    path("school/classes/<int:class_id>/delete/", views.school_class_delete, name="school_class_delete"),
    path("school/sections/", views.school_sections, name="school_sections"),
    path("school/sections/<int:section_id>/edit/", views.school_section_edit, name="school_section_edit"),
    path("school/sections/<int:section_id>/delete/", views.school_section_delete, name="school_section_delete"),
    path("school/subjects/", views.school_subjects, name="school_subjects"),
    path("school/subjects/add/", views.school_subject_add, name="school_subject_add"),
    path("school/subjects/<int:subject_id>/edit/", views.school_subject_edit, name="school_subject_edit"),
    path("school/subjects/<int:subject_id>/delete/", views.school_subject_delete, name="school_subject_delete"),
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