from django.urls import include, path
from . import views

app_name = "core"

urlpatterns = [
    # Marketing / public pages
    path("", views.home, name="home"),
    path("pricing/", views.pricing, name="pricing"),
    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),

    # Role-based dashboard URLs (for login redirect)
    path("superadmin/dashboard/", views.super_admin_dashboard, name="super_admin_dashboard"),
    path("superadmin/enquiries/", views.superadmin_enquiries, name="superadmin_enquiries"),
    path(
        "superadmin/enquiries/<int:enquiry_id>/mark-read/",
        views.superadmin_enquiry_mark_read,
        name="superadmin_enquiry_mark_read",
    ),
    path("school/dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("teacher/dashboard/", views.teacher_dashboard, name="teacher_dashboard"),
    path("student/dashboard/", views.student_dashboard, name="student_dashboard"),

    # Public APIs (super admin)
    path("api/enquiries/unread-count/", views.enquiries_unread_count, name="enquiries_unread_count_api"),

    # Legacy/alias dashboard URLs
    path("super-admin/", views.super_admin_dashboard, name="super_admin_dashboard_legacy"),
    path("school-admin/", views.admin_dashboard, name="admin_dashboard_legacy"),
    path("student-dashboard/", views.student_dashboard, name="student_dashboard_legacy"),
    path("student-dashboard/profile/", views.student_profile, name="student_profile"),
    path("student-dashboard/profile/edit/", views.edit_profile, name="edit_profile"),
    path("student/edit-profile/", views.edit_profile, name="edit_profile_web"),
    path("student-dashboard/marks/", views.student_marks, name="student_marks"),
    path("student/attendance/", views.student_attendance, name="student_attendance"),
    path("student/fees/", views.student_fees, name="student_fees"),
    path("student/exams/", views.student_exams_list, name="student_exams_list"),
    path("student/exam/<int:exam_id>/", views.student_exam_detail_by_id, name="student_exam_detail_by_id"),
    path("student/exam/legacy/<str:exam_name>/", views.student_exam_detail, name="student_exam_detail"),
    path("student/reports/", views.student_reports, name="student_reports"),
    path("student/reports/report-card/<int:exam_id>/", views.student_report_card_view, name="student_report_card_view"),
    path("student/reports/cumulative/", views.student_cumulative_report_view, name="student_cumulative_report_view"),
    path("student/reports/attendance/", views.student_attendance_report_view, name="student_attendance_report_view"),
    path("student/report-card/<int:exam_id>/", views.student_report_card_pdf, name="student_report_card_pdf"),
    path("student/cumulative-report/pdf/", views.student_cumulative_report_pdf, name="student_cumulative_report_pdf"),
    path("student/attendance-report/", views.student_attendance_report_pdf, name="student_attendance_report_pdf"),

    # Teacher Exam Management
    path("teacher/exams/", views.teacher_exams, name="teacher_exams"),
    path("teacher/exams/create/", views.teacher_exam_create, name="teacher_exam_create"),
    path("teacher/exams/<int:exam_id>/", views.teacher_exam_summary, name="teacher_exam_summary"),
    path("teacher/exams/<int:exam_id>/enter-marks/", views.teacher_exam_enter_marks, name="teacher_exam_enter_marks"),
    path("teacher/class-analytics/", views.teacher_class_analytics, name="teacher_class_analytics"),

    # School Admin: Exam list and create
    path("school/exams/", views.school_exams_list, name="school_exams_list"),
    path("school/exams/create/", views.school_exam_create, name="school_exam_create"),
    path("school/exams/session/<int:session_id>/", views.school_exam_session_detail, name="school_exam_session_detail"),

    # Sidebar items (unified)
    path("students/", views.students_list, name="students_list"),
    path("teachers/", views.teachers_list, name="teachers_list"),
    # School Admin: Student management
    path("school/students/", views.school_students_list, name="school_students_list"),
    path("school/students/add/", views.school_student_add, name="school_student_add"),
    path("school/students/<int:student_id>/view/", views.school_student_view, name="school_student_view"),
    path("school/students/<int:student_id>/edit/", views.school_student_edit, name="school_student_edit"),
    path("school/students/<int:student_id>/delete/", views.school_student_delete, name="school_student_delete"),
    path("school/students/import/", views.school_students_import, name="school_students_import"),
    # School Admin: Teacher management
    path("school/teachers/", views.school_teachers_list, name="school_teachers_list"),
    path("school/teachers/add/", views.school_teacher_add, name="school_teacher_add"),
    path("school/teachers/<int:teacher_id>/view/", views.school_teacher_view, name="school_teacher_view"),
    path("school/teachers/<int:teacher_id>/edit/", views.school_teacher_edit, name="school_teacher_edit"),
    path("school/teachers/<int:teacher_id>/delete/", views.school_teacher_delete, name="school_teacher_delete"),
    # School Admin: Academic years, classes, sections, subjects
    path("school/academic-years/", views.school_academic_years, name="school_academic_years"),
    path("school/academic-years/<int:year_id>/edit/", views.school_academic_year_edit, name="school_academic_year_edit"),
    path("school/academic-years/<int:year_id>/set-active/", views.school_academic_year_set_active, name="school_academic_year_set_active"),
    path("school/academic-years/<int:year_id>/delete/", views.school_academic_year_delete, name="school_academic_year_delete"),
    path("school/academic-years/end-and-promote/", views.school_year_end_promote, name="school_year_end_promote"),
    path("school/promote-students/", views.school_promote_students, name="school_promote_students"),
    path("school/classes/", views.school_classes, name="school_classes"),
    path("school/classes/add/", views.school_class_add, name="school_class_add"),
    path("school/classes/<int:class_id>/edit/", views.school_class_edit, name="school_class_edit"),
    path("school/classes/<int:class_id>/delete/", views.school_class_delete, name="school_class_delete"),
    path("school/sections/", views.school_sections, name="school_sections"),
    path("school/sections/add/", views.school_section_add, name="school_section_add"),
    path("school/sections/<int:section_id>/edit/", views.school_section_edit, name="school_section_edit"),
    path("school/sections/<int:section_id>/delete/", views.school_section_delete, name="school_section_delete"),
    path("school/subjects/", views.school_subjects, name="school_subjects"),
    path("school/subjects/add/", views.school_subject_add, name="school_subject_add"),
    path("school/subjects/<int:subject_id>/edit/", views.school_subject_edit, name="school_subject_edit"),
    path("school/subjects/<int:subject_id>/delete/", views.school_subject_delete, name="school_subject_delete"),
    path("attendance/", views.attendance_list, name="attendance_list"),
    path("marks/", views.marks_list, name="marks_list"),
    path("homework/", views.homework_list, name="homework_list"),
    path("school/homework/", views.school_homework_list, name="school_homework_list"),
    path("school/homework/create/", views.school_homework_create, name="school_homework_create"),
    path("student/homework/<int:homework_id>/submit/", views.student_homework_submit, name="student_homework_submit"),
    path("reports/", views.reports_list, name="reports_list"),

    # Teacher actions
    path("teacher/students/", views.teacher_students_list, name="teacher_students_list"),
    path("teacher/homework/create/", views.create_homework, name="create_homework"),
    path("teacher/marks/enter/", views.enter_marks, name="enter_marks"),
    path("teacher/attendance/", views.bulk_attendance, name="bulk_attendance"),
    path("teacher/attendance/mark/", views.mark_attendance, name="mark_attendance"),

    # Fee & Billing (Basic Plan)
    path("school/fees/", views.school_fees_index, name="school_fees_index"),
    path("school/fees/types/", views.school_fee_types, name="school_fee_types"),
    path("school/fees/structure/", views.school_fee_structure, name="school_fee_structure"),
    path("school/fees/add/", views.school_fee_add, name="school_fee_add"),
    path("school/fees/collection/", views.school_fee_collection, name="school_fee_collection"),
    path("school/fees/collect/<int:fee_id>/", views.school_fee_collect, name="school_fee_collect"),
    path("school/fees/receipt/<int:payment_id>/pdf/", views.school_fee_receipt_pdf, name="school_fee_receipt_pdf"),

    # Parent Portal
    path("parent/dashboard/", views.parent_dashboard, name="parent_dashboard"),
    path("parent/student/<int:student_id>/attendance/", views.parent_attendance, name="parent_attendance"),
    path("parent/student/<int:student_id>/marks/", views.parent_marks, name="parent_marks"),
    path("parent/announcements/", views.parent_announcements, name="parent_announcements"),

    # Student ID Card
    path("school/students/<int:student_id>/id-card/pdf/", views.school_student_id_card_pdf, name="school_student_id_card_pdf"),

    # Staff Attendance
    path("school/staff-attendance/", views.school_staff_attendance, name="school_staff_attendance"),
    path("school/staff-attendance/<int:teacher_id>/", views.school_staff_attendance_detail, name="school_staff_attendance_detail"),
    path("school/staff-attendance/mark/", views.school_staff_attendance_mark, name="school_staff_attendance_mark"),

    # Inventory
    path("school/inventory/", views.school_inventory_index, name="school_inventory_index"),
    path("school/inventory/item/add/", views.school_inventory_item_add, name="school_inventory_item_add"),
    path("school/inventory/purchase/add/", views.school_purchase_add, name="school_purchase_add"),
    path("school/invoices/", views.school_invoices_list, name="school_invoices_list"),

    # AI Reports
    path("school/ai-reports/", views.school_ai_reports, name="school_ai_reports"),

    # Support
    path("school/support/", views.school_support_create, name="school_support_create"),

    # Pro Plan: Online Admissions (public + admin)
    path("school/<str:school_code>/admission/apply/", views.online_admission_apply, name="online_admission_apply"),
    path("school/<str:school_code>/admission/status/", views.online_admission_status, name="online_admission_status"),
    path("school/<str:school_code>/results/", views.online_results_view, name="online_results_view"),
    path("school/admissions/", views.school_admissions_list, name="school_admissions_list"),
    path("school/admissions/<int:pk>/approve/", views.school_admission_approve, name="school_admission_approve"),
    path("school/admissions/<int:pk>/reject/", views.school_admission_reject, name="school_admission_reject"),

    # Reports module: mounted in school_erp_demo.urls as namespace `reports`

    # Pro Plan: Toppers alias → reports
    path("school/toppers/", views.school_toppers, name="school_toppers"),
    path("school/library/", views.school_library_index, name="school_library_index"),
    path("school/library/book/add/", views.school_library_book_add, name="school_library_book_add"),
    path("school/library/issue/", views.school_library_issue, name="school_library_issue"),
    path("school/library/return/<int:issue_id>/", views.school_library_return, name="school_library_return"),
    path("school/hostel/", views.school_hostel_index, name="school_hostel_index"),
    path("school/hostel/add/", views.school_hostel_add, name="school_hostel_add"),
    path("school/hostel/<int:hostel_id>/room/add/", views.school_hostel_room_add, name="school_hostel_room_add"),
    path("school/hostel/allocate/", views.school_hostel_allocate, name="school_hostel_allocate"),
    path("school/transport/", views.school_transport_index, name="school_transport_index"),
    path("school/transport/route/add/", views.school_transport_route_add, name="school_transport_route_add"),
    path("school/transport/vehicle/add/", views.school_transport_vehicle_add, name="school_transport_vehicle_add"),
    path("school/transport/assign/", views.school_transport_assign, name="school_transport_assign"),
    path("school/branding/", views.school_branding, name="school_branding"),
]