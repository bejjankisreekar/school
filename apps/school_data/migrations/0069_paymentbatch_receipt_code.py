# Generated manually — auto receipt numbers for PaymentBatch

from django.db import migrations, models


def backfill_receipt_codes(apps, schema_editor):
    PaymentBatch = apps.get_model("school_data", "PaymentBatch")
    for b in PaymentBatch.objects.filter(receipt_code="").iterator():
        y = b.payment_date.year if b.payment_date else b.created_at.year
        code = f"RCPT-{y}-{b.pk:06d}"
        PaymentBatch.objects.filter(pk=b.pk).update(receipt_code=code)


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0068_payment_batch_tender"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentbatch",
            name="receipt_code",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Auto-generated receipt no., e.g. RCPT-2026-000042.",
                max_length=32,
            ),
        ),
        migrations.RunPython(backfill_receipt_codes, migrations.RunPython.noop),
    ]
