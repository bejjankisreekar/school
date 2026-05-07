from calendar import monthrange
from datetime import date, timedelta

from django.db import migrations, models


def forwards_school_free(apps, schema_editor):
    School = apps.get_model("customers", "School")
    for row in School.objects.exclude(saas_billing_complimentary_until=None).iterator():
        if getattr(row, "saas_free_until_date", None):
            continue
        row.saas_free_until_date = row.saas_billing_complimentary_until
        row.save(update_fields=["saas_free_until_date"])


def forwards_invoice_period(apps, schema_editor):
    SchoolGeneratedInvoice = apps.get_model("customers", "SchoolGeneratedInvoice")
    for inv in SchoolGeneratedInvoice.objects.all().iterator():
        snap = inv.snapshot or {}
        y = snap.get("invoice_period_year")
        m = snap.get("invoice_period_month")
        try:
            if y is not None and m is not None:
                yi, mi = int(y), int(m)
                inv.billing_period_year = yi
                inv.billing_period_month = mi
                inv.invoice_month_key = f"{yi:04d}-{mi:02d}"
            elif y is not None:
                yi = int(y)
                inv.billing_period_year = yi
                inv.billing_period_month = None
                inv.invoice_month_key = f"{yi:04d}-00"
            else:
                d = inv.created_at.date()
                inv.billing_period_year = d.year
                inv.billing_period_month = d.month
                inv.invoice_month_key = f"{d.year:04d}-{d.month:02d}"
        except (TypeError, ValueError):
            d = inv.created_at.date()
            inv.billing_period_year = d.year
            inv.billing_period_month = d.month
            inv.invoice_month_key = f"{d.year:04d}-{d.month:02d}"
        if not inv.due_date and inv.billing_period_year:
            if inv.billing_period_month and 1 <= inv.billing_period_month <= 12:
                y0, m0 = inv.billing_period_year, inv.billing_period_month
                last_d = monthrange(y0, m0)[1]
                inv.due_date = date(y0, m0, last_d) + timedelta(days=14)
            else:
                inv.due_date = date(inv.billing_period_year, 12, 31) + timedelta(days=14)
        inv.save(
            update_fields=[
                "billing_period_year",
                "billing_period_month",
                "invoice_month_key",
                "due_date",
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0023_school_saas_billing_complimentary_until"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="saas_service_start_date",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Optional contract anchor: billing periods before this month are not issued unless overridden.",
            ),
        ),
        migrations.AddField(
            model_name="school",
            name="saas_free_until_date",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Inclusive last day of free service; invoices for calendar periods ending on/before this date are blocked unless overridden.",
            ),
        ),
        migrations.AlterField(
            model_name="school",
            name="saas_billing_complimentary_until",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Legacy: through this date (inclusive), invoices may be ₹0. Prefer saas_free_until_date.",
            ),
        ),
        migrations.AddField(
            model_name="schoolgeneratedinvoice",
            name="billing_period_year",
            field=models.PositiveSmallIntegerField(
                blank=True,
                db_index=True,
                null=True,
                help_text="Calendar year of the service month/year this invoice covers.",
            ),
        ),
        migrations.AddField(
            model_name="schoolgeneratedinvoice",
            name="billing_period_month",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                help_text="1–12 for monthly cycle; null for yearly (see invoice_month_key).",
            ),
        ),
        migrations.AddField(
            model_name="schoolgeneratedinvoice",
            name="invoice_month_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                max_length=8,
                help_text="Stable period id, e.g. 2026-05 or 2026-00 for annual.",
            ),
        ),
        migrations.AddField(
            model_name="schoolgeneratedinvoice",
            name="due_date",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Payment due date for tracking overdue vs invoice period (independent of paid_at).",
            ),
        ),
        migrations.RunPython(forwards_school_free, migrations.RunPython.noop),
        migrations.RunPython(forwards_invoice_period, migrations.RunPython.noop),
    ]
