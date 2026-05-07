from django.apps import AppConfig


class SuperAdminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.super_admin"
    verbose_name = "Super Admin (SaaS Control)"

