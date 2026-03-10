from django.urls import path
from . import views

app_name = "payroll"

urlpatterns = [
    path("school/payroll/", views.payroll_dashboard, name="payroll_dashboard"),
    path("school/payroll/components/", views.salary_components_list, name="salary_components_list"),
    path("school/payroll/components/add/", views.salary_component_add, name="salary_component_add"),
    path("school/payroll/components/<int:pk>/edit/", views.salary_component_edit, name="salary_component_edit"),
    path("school/payroll/components/<int:pk>/delete/", views.salary_component_delete, name="salary_component_delete"),
    path("school/payroll/salary-structure/", views.salary_structure_list, name="salary_structure_list"),
    path("school/payroll/salary-structure/add/", views.salary_structure_add, name="salary_structure_add"),
    path("school/payroll/salary-structure/<int:pk>/edit/", views.salary_structure_edit, name="salary_structure_edit"),
    path("school/payroll/salary-structure/<int:pk>/delete/", views.salary_structure_delete, name="salary_structure_delete"),
    path("school/payroll/advances/", views.salary_advances_list, name="salary_advances_list"),
    path("school/payroll/advances/add/", views.salary_advance_add, name="salary_advance_add"),
    path("school/payroll/advances/<int:pk>/edit/", views.salary_advance_edit, name="salary_advance_edit"),
    path("school/payroll/advances/<int:pk>/delete/", views.salary_advance_delete, name="salary_advance_delete"),
    path("school/payroll/generate/", views.payroll_generate, name="payroll_generate"),
    path("school/payslips/", views.payslips_list, name="payslips_list"),
    path("school/payslips/<int:pk>/", views.payslip_view, name="payslip_view"),
    path("school/payslips/<int:pk>/pdf/", views.payslip_pdf, name="payslip_pdf"),
]
