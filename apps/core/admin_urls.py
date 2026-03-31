"""URLs for /admin/schools/, /admin/teachers/, /admin/students/ — SuperAdmin frontend management."""
from django.shortcuts import redirect
from django.urls import path
from . import admin_views

app_name = "admin_manage"

urlpatterns = [
    path("", lambda r: redirect("admin_manage:schools_list")),
    path("schools/", admin_views.admin_schools_list, name="schools_list"),
    path("schools/create/", admin_views.admin_school_create, name="school_create"),
    path("schools/<str:school_code>/view/", admin_views.admin_school_view, name="school_view"),
    path("schools/<str:school_code>/edit/", admin_views.admin_school_edit, name="school_edit"),
    path("school-plans/", admin_views.admin_school_plans_list, name="school_plans_list"),
    path("school-plans/<str:school_code>/change-plan/", admin_views.admin_school_change_plan, name="school_change_plan"),
    path("school-plans/<str:school_code>/manage-features/", admin_views.admin_school_manage_features, name="school_manage_features"),
    path("billing/plans/", admin_views.admin_billing_plans_list, name="billing_plans_list"),
    path("billing/coupons/", admin_views.admin_coupons_list, name="coupons_list"),
    path("billing/coupons/create/", admin_views.admin_coupon_create, name="coupon_create"),
    path("billing/coupons/<int:pk>/edit/", admin_views.admin_coupon_edit, name="coupon_edit"),
    path("teachers/", admin_views.admin_teachers_list, name="teachers_list"),
    path("teachers/create/", admin_views.admin_teacher_create, name="teacher_create"),
    path("teachers/<str:school_code>/<int:teacher_id>/view/", admin_views.admin_teacher_view, name="teacher_view"),
    path("teachers/<str:school_code>/<int:teacher_id>/edit/", admin_views.admin_teacher_edit, name="teacher_edit"),
    path("students/", admin_views.admin_students_list, name="students_list"),
    path("students/create/", admin_views.admin_student_create, name="student_create"),
    path("students/<str:school_code>/<int:student_id>/view/", admin_views.admin_student_view, name="student_view"),
    path("students/<str:school_code>/<int:student_id>/edit/", admin_views.admin_student_edit, name="student_edit"),
]
