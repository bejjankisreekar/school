from django.urls import include, path
from django.views.generic import RedirectView

from . import billing_views, views

app_name = "core"

urlpatterns = [
    # Marketing / public pages
    path("", views.home, name="home"),
    path("pricing/", views.pricing, name="pricing"),
    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),
    path("enroll/", views.school_enrollment_signup, name="school_enroll"),

    # Role-based dashboard URLs (for login redirect)
    path("superadmin/dashboard/", views.super_admin_dashboard, name="super_admin_dashboard"),
    path(
        "superadmin/platform/footprint/",
        views.superadmin_platform_footprint,
        name="superadmin_platform_footprint",
    ),
    path("superadmin/students/", views.superadmin_global_students, name="superadmin_global_students"),
    path("superadmin/teachers/", views.superadmin_global_teachers, name="superadmin_global_teachers"),
    path(
        "superadmin/schools/<int:school_id>/financial/",
        views.superadmin_school_financial,
        name="superadmin_school_financial",
    ),
    path(
        "superadmin/billing/platform-invoices/<int:invoice_id>/pdf/",
        views.superadmin_platform_invoice_pdf,
        name="superadmin_platform_invoice_pdf",
    ),
    path("superadmin/enquiries/", views.superadmin_enquiries, name="superadmin_enquiries"),
    path(
        "superadmin/enquiries/<int:enquiry_id>/mark-read/",
        views.superadmin_enquiry_mark_read,
        name="superadmin_enquiry_mark_read",
    ),
    path("superadmin/financials/", views.superadmin_financials, name="superadmin_financials"),
    path(
        "superadmin/payments/subscriptions/",
        views.superadmin_subscription_payments,
        name="superadmin_subscription_payments",
    ),
    path(
        "superadmin/payments/subscriptions/record/",
        views.superadmin_record_subscription_payment,
        name="superadmin_record_subscription_payment",
    ),
    path(
        "superadmin/payments/subscriptions/<int:pk>/edit/",
        views.superadmin_edit_subscription_payment,
        name="superadmin_edit_subscription_payment",
    ),
    path(
        "superadmin/payments/subscriptions/<int:pk>/delete/",
        views.superadmin_delete_subscription_payment,
        name="superadmin_delete_subscription_payment",
    ),
    path(
        "superadmin/billing/sales/",
        views.superadmin_billing_sales,
        name="superadmin_billing_sales",
    ),
    path(
        "superadmin/billing/invoices/",
        views.superadmin_billing_invoices,
        name="superadmin_billing_invoices",
    ),
    path(
        "superadmin/billing/invoices/<int:invoice_id>/pay/",
        views.superadmin_billing_invoice_pay,
        name="superadmin_billing_invoice_pay",
    ),
    path(
        "superadmin/billing/receipts/<int:receipt_id>/pdf/",
        views.superadmin_billing_receipt_pdf,
        name="superadmin_billing_receipt_pdf",
    ),
    path("superadmin/enrollments/", views.superadmin_enrollments, name="superadmin_enrollments"),
    path(
        "superadmin/enrollments/<int:pk>/",
        views.superadmin_enrollment_detail,
        name="superadmin_enrollment_detail",
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
    path(
        "student/exam-session/<int:session_id>/",
        views.student_exam_session_detail,
        name="student_exam_session_detail",
    ),
    path("student/exam/<int:exam_id>/", views.student_exam_detail_by_id, name="student_exam_detail_by_id"),
    path("student/exam/legacy/<str:exam_name>/", views.student_exam_detail, name="student_exam_detail"),
    path("student/reports/", views.student_reports, name="student_reports"),
    path("student/reports/report-card/<int:exam_id>/", views.student_report_card_view, name="student_report_card_view"),
    path(
        "student/reports/report-card/session/<int:session_id>/",
        views.student_report_card_session_view,
        name="student_report_card_session_view",
    ),
    path("student/reports/cumulative/", views.student_cumulative_report_view, name="student_cumulative_report_view"),
    path("student/reports/attendance/", views.student_attendance_report_view, name="student_attendance_report_view"),
    path("student/report-card/<int:exam_id>/", views.student_report_card_pdf, name="student_report_card_pdf"),
    path(
        "student/report-card/session/<int:session_id>/",
        views.student_report_card_session_pdf,
        name="student_report_card_session_pdf",
    ),
    path("student/cumulative-report/pdf/", views.student_cumulative_report_pdf, name="student_cumulative_report_pdf"),
    path("student/attendance-report/", views.student_attendance_report_pdf, name="student_attendance_report_pdf"),

    # Teacher Exam Management
    path("teacher/exams/", views.teacher_exams, name="teacher_exams"),
    path("teacher/exams/create/", views.teacher_exam_create, name="teacher_exam_create"),
    path(
        "teacher/exams/session/<int:session_id>/",
        views.teacher_exam_session_detail,
        name="teacher_exam_session_detail",
    ),
    path("teacher/exams/<int:exam_id>/", views.teacher_exam_summary, name="teacher_exam_summary"),
    path("teacher/exams/<int:exam_id>/enter-marks/", views.teacher_exam_enter_marks, name="teacher_exam_enter_marks"),
    path("teacher/class-analytics/", views.teacher_class_analytics, name="teacher_class_analytics"),

    # School Admin: Exam list and create
    path("school/exams/", views.school_exams_list, name="school_exams_list"),
    path("school/exams/create/", views.school_exam_create, name="school_exam_create"),
    path(
        "school/exams/session/<int:session_id>/edit/",
        views.school_exam_session_edit,
        name="school_exam_session_edit",
    ),
    path(
        "school/exams/session/<int:session_id>/delete/",
        views.school_exam_session_delete,
        name="school_exam_session_delete",
    ),
    path("school/exams/session/<int:session_id>/", views.school_exam_session_detail, name="school_exam_session_detail"),
    path(
        "school/exams/session/<int:session_id>/marks-lock-all/",
        views.school_exam_session_set_all_marks_lock,
        name="school_exam_session_set_all_marks_lock",
    ),
    path(
        "school/exams/paper/<int:exam_id>/marks/",
        views.school_exam_paper_enter_marks,
        name="school_exam_paper_enter_marks",
    ),
    path(
        "school/exams/paper/<int:exam_id>/marks-lock/",
        views.school_exam_paper_set_marks_lock,
        name="school_exam_paper_set_marks_lock",
    ),

    # Sidebar items (unified)
    path("students/", views.students_list, name="students_list"),
    path("teachers/", views.teachers_list, name="teachers_list"),
    # School Admin: Student management
    path("school/students/", views.school_students_list, name="school_students_list"),
    path("school/students/add/", views.school_student_add, name="school_student_add"),
    path("school/students/<int:student_id>/view/", views.school_student_view, name="school_student_view"),
    path("school/students/<int:student_id>/profile/pdf/", views.school_student_profile_pdf, name="school_student_profile_pdf"),
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
    path("school/academic-years/add/", views.school_academic_year_add, name="school_academic_year_add"),
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
    path("school/calendar/holidays/", views.school_calendar_holidays, name="school_calendar_holidays"),
    path("attendance/", views.attendance_list, name="attendance_list"),
    path("marks/", views.marks_list, name="marks_list"),
    path("homework/", views.homework_list, name="homework_list"),
    path("school/homework/", views.school_homework_list, name="school_homework_list"),
    path("school/homework/create/", views.school_homework_create, name="school_homework_create"),
    path(
        "school/homework/<int:pk>/update/",
        views.school_homework_update,
        name="school_homework_update",
    ),
    path(
        "school/homework/<int:pk>/delete/",
        views.school_homework_delete,
        name="school_homework_delete",
    ),
    path("student/homework/<int:homework_id>/submit/", views.student_homework_submit, name="student_homework_submit"),
    path("reports/", views.reports_list, name="reports_list"),

    # Teacher actions
    path("teacher/students/", views.teacher_students_list, name="teacher_students_list"),
    path("teacher/homework/create/", views.create_homework, name="create_homework"),
    path("teacher/marks/enter/", views.enter_marks, name="enter_marks"),
    path("teacher/attendance/", views.bulk_attendance, name="bulk_attendance"),
    path("teacher/attendance/mark/", views.mark_attendance, name="mark_attendance"),

    # Fees & Billing (SaaS module)
    path("school/billing/", billing_views.billing_dashboard, name="billing_dashboard"),
    path(
        "school/billing/record-payment/",
        billing_views.billing_record_payment,
        name="billing_record_payment",
    ),
    path(
        "school/billing/api/students-search/",
        billing_views.billing_fee_student_search,
        name="billing_fee_student_search",
    ),
    path(
        "school/billing/collect/student/<int:student_id>/",
        billing_views.billing_student_collect,
        name="billing_student_collect",
    ),
    path(
        "school/billing/fee-structure/",
        billing_views.billing_class_fee_structure,
        name="billing_fee_structure",
    ),
    path(
        "school/billing/fee-structure/impacted-count/",
        billing_views.billing_structure_impacted_count,
        name="billing_structure_impacted_count",
    ),
    path(
        "school/billing/fee-structure/class/<int:classroom_id>/students/",
        billing_views.billing_class_fee_students,
        name="billing_class_fee_students",
    ),
    path(
        "school/billing/fee-structure/class/<int:classroom_id>/student/<int:student_id>/fees/",
        billing_views.billing_student_fee_lines,
        name="billing_student_fee_lines",
    ),
    path(
        "school/billing/fee-categories/",
        billing_views.billing_fee_categories,
        name="billing_fee_categories",
    ),
    path("school/billing/concessions/", billing_views.billing_concessions, name="billing_concessions"),
    path(
        "school/billing/fee-master/",
        RedirectView.as_view(pattern_name="core:billing_fee_structure", permanent=False),
        name="billing_fee_master",
    ),
    path(
        "school/billing/fee-master/types/<int:pk>/update/",
        views.school_fee_type_update,
        name="school_fee_type_update",
    ),
    path(
        "school/billing/fee-master/types/<int:pk>/delete/",
        views.school_fee_type_delete,
        name="school_fee_type_delete",
    ),
    path(
        "school/billing/fee-master/apply/<int:structure_id>/",
        views.school_fee_structure_apply,
        name="billing_structure_apply",
    ),
    path(
        "school/billing/assignment/",
        RedirectView.as_view(pattern_name="core:billing_fee_structure", permanent=False),
        name="billing_assignment",
    ),
    path(
        "school/billing/collection/students-search/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_collection_students_search",
    ),
    path(
        "school/billing/collection/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_collection",
    ),
    path(
        "school/billing/collect/<int:fee_id>/",
        views.redirect_billing_dashboard,
        name="billing_collect",
    ),
    path(
        "school/billing/pending-dues/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_pending_dues",
    ),
    path(
        "school/billing/installments/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_installments",
    ),
    path(
        "school/billing/discounts/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_discounts",
    ),
    path(
        "school/billing/late-fines/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_late_fines",
    ),
    path(
        "school/billing/receipts/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_receipts",
    ),
    path(
        "school/billing/receipts/export.csv",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_receipts_export_csv",
    ),
    path(
        "school/billing/ledger/export.csv",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_ledger_export_csv",
    ),
    path(
        "school/billing/receipt/<int:payment_id>/pdf/",
        views.redirect_billing_dashboard,
        name="billing_receipt_pdf",
    ),
    path(
        "school/billing/refunds/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_refunds",
    ),
    path(
        "school/billing/reports/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_reports",
    ),
    path(
        "school/billing/gateway/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_gateway",
    ),
    path(
        "school/billing/parent-portal/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="billing_parent_portal",
    ),
    # Legacy /school/fees/* → new module
    path(
        "school/fees/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="school_fees_index",
    ),
    path(
        "school/fees/types/",
        RedirectView.as_view(pattern_name="core:billing_fee_structure", permanent=False),
        name="school_fee_types",
    ),
    path("school/fees/structure/", views.school_fee_structure, name="school_fee_structure"),
    path(
        "school/fees/add/",
        RedirectView.as_view(pattern_name="core:billing_fee_structure", permanent=False),
        name="school_fee_add",
    ),
    path(
        "school/fees/collection/",
        RedirectView.as_view(pattern_name="core:billing_record_payment", permanent=False),
        name="school_fee_collection",
    ),
    path(
        "school/fees/payments/",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="school_fee_payments",
    ),
    path(
        "school/fees/payments/export.csv",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="school_fee_payments_export_csv",
    ),
    path(
        "school/fees/ledger/export.csv",
        RedirectView.as_view(pattern_name="core:billing_dashboard", permanent=False),
        name="school_fee_ledger_export_csv",
    ),
    path(
        "school/fees/collect/<int:fee_id>/",
        views.school_fee_collect_redirect,
        name="school_fee_collect",
    ),
    path(
        "school/fees/receipt/<int:payment_id>/pdf/",
        views.redirect_billing_dashboard,
        name="school_fee_receipt_pdf",
    ),

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