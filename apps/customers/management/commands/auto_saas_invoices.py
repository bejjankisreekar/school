"""
Create missing Control Center SaaS invoices for the previous calendar period.

Run monthly (e.g. cron on the 1st) after enabling ``SAAS_BILLING_AUTO_INVOICE_ENABLED``.

Examples::

    python manage.py auto_saas_invoices --dry-run
    python manage.py auto_saas_invoices --force
"""

from __future__ import annotations

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from apps.customers.models import School, SchoolGeneratedInvoice
from apps.super_admin.views import _billing_generate_invoice_response


class Command(BaseCommand):
    help = "Auto-generate missing SaaS Control Center invoices for the previous billing period."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Log actions only; do not create invoices.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even when SAAS_BILLING_AUTO_INVOICE_ENABLED is False.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        force = options["force"]
        if not getattr(settings, "SAAS_BILLING_AUTO_INVOICE_ENABLED", False) and not force:
            self.stderr.write(
                "SAAS_BILLING_AUTO_INVOICE_ENABLED is False; nothing to do (use --force to override)."
            )
            return

        User = get_user_model()
        actor = User.objects.filter(is_superuser=True, is_active=True).first()
        if actor is None:
            self.stderr.write("No active superuser found; cannot attribute audit logs.")
            return

        connection.set_schema_to_public()
        today = timezone.localdate()
        prev_y, prev_m = _prev_month(today.year, today.month)
        include_gst = bool(getattr(settings, "SAAS_BILLING_AUTO_INVOICE_INCLUDE_GST", False))
        respect_renew = bool(getattr(settings, "SAAS_BILLING_AUTO_INVOICE_RESPECT_AUTO_RENEW", True))

        qs = (
            School.objects.exclude(schema_name="public")
            .select_related("plan")
            .filter(school_status__in=[School.SchoolStatus.ACTIVE, School.SchoolStatus.TRIAL])
        )
        created = skipped = errors = 0
        for school in qs.order_by("id"):
            if respect_renew and not school.saas_billing_auto_renew:
                skipped += 1
                continue

            if school.saas_billing_cycle == School.SaaSBillingCycle.YEARLY:
                if today.month != 1:
                    skipped += 1
                    continue
                bill_year = today.year - 1
                inv_key = f"{bill_year:04d}-00"
                payload = {
                    "billing_year": bill_year,
                    "include_gst": include_gst,
                    "automation_scheduled": True,
                }
            else:
                inv_key = f"{prev_y:04d}-{prev_m:02d}"
                payload = {
                    "billing_year": prev_y,
                    "billing_month": prev_m,
                    "include_gst": include_gst,
                    "automation_scheduled": True,
                }

            exists = SchoolGeneratedInvoice.objects.filter(
                school_id=school.pk,
                invoice_month_key=inv_key,
            ).exclude(status=SchoolGeneratedInvoice.Status.VOID)
            if exists.exists():
                skipped += 1
                continue

            if dry:
                self.stdout.write(f"[dry-run] would generate {inv_key} for school {school.pk} {school.name}")
                created += 1
                continue

            resp = _billing_generate_invoice_response(school, actor, payload)
            try:
                data = json.loads(resp.content.decode())
            except Exception:
                errors += 1
                self.stderr.write(f"School {school.pk}: invalid response from generator")
                continue
            if data.get("ok"):
                created += 1
                self.stdout.write(f"Created {inv_key} for school {school.pk} {school.name}")
            else:
                skipped += 1
                self.stdout.write(f"Skip school {school.pk} {school.name}: {data.get('error')}")

        self.stdout.write(
            self.style.NOTICE(f"Done. created={created} skipped_or_blocked={skipped} errors={errors}")
        )


def _prev_month(year: int, month: int) -> tuple[int, int]:
    if month <= 1:
        return year - 1, 12
    return year, month - 1
