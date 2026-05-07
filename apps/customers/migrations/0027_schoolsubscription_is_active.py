# Generated manually for plan-based access control.

from django.db import migrations, models


def forwards_set_is_active(apps, schema_editor):
    SchoolSubscription = apps.get_model("customers", "SchoolSubscription")
    active_status = "active"
    for row in SchoolSubscription.objects.all().only("id", "status", "is_active"):
        row.is_active = (row.status or "").lower() == active_status
        row.save(update_fields=["is_active"])


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0026_saasplatformpayment_school_generated_invoice"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolsubscription",
            name="is_active",
            field=models.BooleanField(
                default=True,
                db_index=True,
                help_text="Mirrors an active billing row; false when subscription is paused or ended administratively.",
            ),
        ),
        migrations.RunPython(forwards_set_is_active, migrations.RunPython.noop),
    ]
