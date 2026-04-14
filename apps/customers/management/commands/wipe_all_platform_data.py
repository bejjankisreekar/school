"""
Wipe all school tenants (PostgreSQL schemas + timetable/school_data/payroll data),
then remove all users and public-schema operational records.

Uses raw SQL for tenant removal so a broken public DB (missing notification tables,
stray school_data_* in public) does not block drops.

Keeps: migration history, contenttypes, auth.Permission, Groups,
      customers Plan/Feature/SubscriptionPlan/Coupon, core.Plan (legacy).

Preserves the public platform tenant row (schema_name='public') if present.

Usage:
  python manage.py wipe_all_platform_data --skip-checks
  python manage.py wipe_all_platform_data --execute --skip-checks
"""
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.utils import ProgrammingError
from django_tenants.utils import get_public_schema_name
from psycopg2 import sql as psql

from apps.core.models import ContactEnquiry, SchoolEnrollmentRequest, SchoolSubscription
from apps.customers.models import Domain, PlatformSettings, School
from apps.notifications.models import NotificationTemplate


def _safe_count(qs) -> int | str:
    try:
        return qs.count()
    except ProgrammingError:
        return "n/a (table missing)"


def _safe_delete_qs(qs, label: str, stdout, style) -> None:
    try:
        n, _ = qs.delete()
        stdout.write(style.SUCCESS(f"Deleted {n} {label} row(s)."))
    except ProgrammingError:
        stdout.write(style.WARNING(f"Skipped {label} (table missing)."))


def _public_table_names(cursor, public: str) -> set[str]:
    cursor.execute(
        """
        SELECT tablename FROM pg_tables WHERE schemaname = %s
        """,
        [public],
    )
    return {row[0] for row in cursor.fetchall()}


def _drop_schema(cursor, schema_name: str) -> None:
    cursor.execute(
        psql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(psql.Identifier(schema_name))
    )


def _delete_from_if_exists(cursor, public: str, table: str, tables: set[str]) -> None:
    if table not in tables:
        return
    cursor.execute(
        psql.SQL("DELETE FROM {}.{}").format(
            psql.Identifier(public),
            psql.Identifier(table),
        )
    )


class Command(BaseCommand):
    help = "Drop all tenant schemas and delete users + public operational data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Actually run deletions (default is dry-run).",
        )

    def handle(self, *args, **options):
        execute = options["execute"]
        public = get_public_schema_name()
        connection.set_schema_to_public()

        User = get_user_model()
        schema_names = list(
            School.objects.exclude(schema_name=public).values_list(
                "schema_name", flat=True
            )
        )
        user_count = _safe_count(User.objects)
        domain_count = _safe_count(
            Domain.objects.exclude(tenant__schema_name=public)
        )

        self.stdout.write(f"Tenant PostgreSQL schemas to drop: {len(schema_names)}")
        self.stdout.write(f"Users to delete: {user_count}")
        self.stdout.write(f"Domains (non-public tenants): {domain_count}")
        self.stdout.write(
            f"Contact enquiries: {_safe_count(ContactEnquiry.objects)}"
        )
        self.stdout.write(
            f"Enrollment requests: {_safe_count(SchoolEnrollmentRequest.objects)}"
        )
        self.stdout.write(
            f"Core SchoolSubscription rows: {_safe_count(SchoolSubscription.objects)}"
        )
        self.stdout.write(
            f"Notification templates: {_safe_count(NotificationTemplate.objects)}"
        )
        self.stdout.write(
            f"Platform settings keys: {_safe_count(PlatformSettings.objects)}"
        )
        self.stdout.write(f"Sessions: {_safe_count(Session.objects)}")

        if not execute:
            self.stdout.write(
                self.style.WARNING(
                    "Dry-run. Re-run with --execute to wipe everything listed above."
                )
            )
            return

        with connection.cursor() as cursor:
            # 1) Drop every tenant schema (all timetable/school_data/payroll data)
            for sn in schema_names:
                try:
                    _drop_schema(cursor, sn)
                    self.stdout.write(
                        self.style.SUCCESS(f"Dropped schema {sn!r}")
                    )
                except Exception as exc:
                    self.stdout.write(
                        self.style.ERROR(f"Could not drop schema {sn!r}: {exc}")
                    )

            tables = _public_table_names(cursor, public)

            # 2) Stray tenant-only tables in public
            stray = [
                t
                for t in sorted(tables)
                if t.startswith("school_data_")
                or t.startswith("timetable_")
                or t.startswith("payroll_")
            ]
            for t in stray:
                cursor.execute(
                    psql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(
                        psql.Identifier(public),
                        psql.Identifier(t),
                    )
                )
                self.stdout.write(self.style.WARNING(f"Dropped stray public.{t}"))

            tables = _public_table_names(cursor, public)

            # 3) Notifications (public); order respects FKs when tables exist
            for t in (
                "notifications_studentnotificationread",
                "notifications_notificationlog",
                "notifications_schoolsmscredit",
                "notifications_notificationtemplate",
            ):
                _delete_from_if_exists(cursor, public, t, tables)

            # 4) Platform billing rows (some DBs use ON DELETE RESTRICT toward school)
            for t in (
                "saas_billing_receipts",
                "saas_invoice_payments",
                "saas_invoices",
                "customers_saasplatformpayment",
                "customers_schoolsubscription",
            ):
                _delete_from_if_exists(cursor, public, t, tables)

            tables = _public_table_names(cursor, public)

            # 5) Users before school rows (FK: accounts_user.school_id -> customers_school.code)
            _delete_from_if_exists(cursor, public, "django_session", tables)
            if "accounts_user" in tables:
                cursor.execute(
                    psql.SQL("TRUNCATE TABLE {}.{} RESTART IDENTITY CASCADE").format(
                        psql.Identifier(public),
                        psql.Identifier("accounts_user"),
                    )
                )
                self.stdout.write(self.style.SUCCESS("Truncated accounts_user (and dependent rows)."))

            tables = _public_table_names(cursor, public)

            # 6) Remove non-public school rows (domains CASCADE at DB level when present)
            if "customers_school" in tables:
                cursor.execute(
                    f'DELETE FROM "{public}"."customers_school" WHERE "schema_name" <> %s',
                    [public],
                )
                self.stdout.write(
                    self.style.SUCCESS("Removed non-public customers_school rows.")
                )

        connection.set_schema_to_public()

        # 6) Remaining public data via ORM (tables expected to exist on a normal install)
        _safe_delete_qs(ContactEnquiry.objects.all(), "contact enquiry", self.stdout, self.style)
        _safe_delete_qs(
            SchoolEnrollmentRequest.objects.all(), "enrollment request", self.stdout, self.style
        )
        _safe_delete_qs(
            SchoolSubscription.objects.all(), "core school subscription", self.stdout, self.style
        )
        _safe_delete_qs(
            NotificationTemplate.objects.all(), "notification template", self.stdout, self.style
        )
        _safe_delete_qs(PlatformSettings.objects.all(), "platform setting", self.stdout, self.style)

        self.stdout.write(
            self.style.SUCCESS(
                "Wipe finished. Create a superuser: python manage.py createsuperuser\n"
                "If localhost no longer resolves, run: python manage.py setup_public_tenant\n"
                "Ensure SaaS tiers exist: python manage.py seed_saas_plans"
            )
        )
