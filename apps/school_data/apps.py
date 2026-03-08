from django.apps import AppConfig


class SchoolDataConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.school_data"
    label = "school_data"
    verbose_name = "School Data (Tenant)"
