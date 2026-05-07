"""
Compatibility shim for Django system checks.

This project uses django-tenants, which replaces the default `migrate` behavior.
Django 6.x expects the `migrate` command to expose an `autodetector` attribute
for an internal check that compares it with `makemigrations.autodetector`.

Some django-tenants command versions omit that attribute, causing *all* management
commands to crash during `BaseCommand.check()`.
"""

from django.db.migrations.autodetector import MigrationAutodetector

try:
    from django_tenants.management.commands.migrate_schemas import Command as MigrateSchemasCommand
except Exception:  # pragma: no cover
    MigrateSchemasCommand = None


if MigrateSchemasCommand is not None:

    class Command(MigrateSchemasCommand):
        autodetector = MigrationAutodetector

