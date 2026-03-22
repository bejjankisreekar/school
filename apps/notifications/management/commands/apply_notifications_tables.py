"""
Create notifications tables (NotificationLog, SchoolSMSCredit) in all schemas.

Use this when regular Django migrations cannot run cleanly.

Run:
    python manage.py apply_notifications_tables
"""
from django.core.management.base import BaseCommand
from django.db import connection

from django_tenants.utils import get_tenant_model, schema_context, get_public_schema_name


CREATE_NOTIFICATIONLOG_SQL = """
CREATE TABLE IF NOT EXISTS notifications_notificationlog (
    id BIGSERIAL PRIMARY KEY,
    school_id BIGINT NOT NULL REFERENCES customers_school(id) ON DELETE CASCADE,
    sender_id BIGINT NULL REFERENCES accounts_user(id) ON DELETE SET NULL,
    sender_role VARCHAR(20) NOT NULL,
    channel VARCHAR(10) NOT NULL,
    target_type VARCHAR(20) NOT NULL,
    target_class_id BIGINT NULL REFERENCES school_data_classroom(id) ON DELETE SET NULL,
    target_section_id BIGINT NULL REFERENCES school_data_section(id) ON DELETE SET NULL,
    target_student_id BIGINT NULL REFERENCES school_data_student(id) ON DELETE SET NULL,
    recipient_name VARCHAR(255) NOT NULL DEFAULT '',
    recipient_phone VARCHAR(20) NOT NULL DEFAULT '',
    recipient_email VARCHAR(254) NOT NULL DEFAULT '',
    subject VARCHAR(255) NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ NULL
);
"""

CREATE_SCHOOLSMS_SQL = """
CREATE TABLE IF NOT EXISTS notifications_schoolsmscredit (
    id BIGSERIAL PRIMARY KEY,
    school_id BIGINT NOT NULL UNIQUE REFERENCES customers_school(id) ON DELETE CASCADE,
    balance INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


class Command(BaseCommand):
    help = "Create notifications tables in public and tenant schemas (raw SQL)."

    def handle(self, *args, **options):
        TenantModel = get_tenant_model()
        public_schema = get_public_schema_name()

        # Tenant schemas only (skip public)
        for tenant in TenantModel.objects.all():
            if tenant.schema_name == public_schema:
                continue
            self.stdout.write(f"Applying notifications tables in schema: {tenant.schema_name}")
            with schema_context(tenant.schema_name):
                self._create_tables()

        # Record a fake migration so Django thinks 0001_initial is applied
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO django_migrations (app, name, applied)
                SELECT 'notifications', '0001_initial', NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM django_migrations
                    WHERE app = 'notifications' AND name = '0001_initial'
                );
                """
            )

        self.stdout.write(self.style.SUCCESS("Notifications tables applied in all schemas."))

    def _create_tables(self):
        with connection.cursor() as cursor:
            cursor.execute(CREATE_NOTIFICATIONLOG_SQL)
            cursor.execute(CREATE_SCHOOLSMS_SQL)

