from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"

    def ready(self):
        # django-tenants compatibility: Django 6.x expects the migrate command
        # to expose `autodetector`. Some django-tenants versions omit it and
        # Django crashes during system checks for *all* management commands.
        try:
            from django.db.migrations.autodetector import MigrationAutodetector
            from django_tenants.management.commands.migrate_schemas import Command as MigrateSchemasCommand

            if not hasattr(MigrateSchemasCommand, "autodetector"):
                MigrateSchemasCommand.autodetector = MigrationAutodetector
        except Exception:
            pass
