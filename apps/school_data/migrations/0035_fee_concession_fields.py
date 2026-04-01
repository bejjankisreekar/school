# Generated manually for per-line fee concessions

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0034_fee_structure_billing_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="fee",
            name="concession_fixed",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                help_text="Flat discount off this fee line (after percentage, if any).",
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="fee",
            name="concession_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                help_text="Percentage discount on the original fee amount (0–100).",
                max_digits=5,
            ),
        ),
        migrations.AddField(
            model_name="fee",
            name="concession_kind",
            field=models.CharField(
                choices=[
                    ("NONE", "Fixed / percentage only"),
                    ("MANUAL", "Manual concession"),
                    ("SCHOLARSHIP", "Scholarship"),
                    ("SIBLING", "Sibling discount"),
                    ("STAFF_CHILD", "Staff child"),
                ],
                default="NONE",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="fee",
            name="concession_note",
            field=models.TextField(blank=True, default=""),
        ),
    ]
