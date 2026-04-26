# Generated manually for fee structure batch grouping

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0064_subject_display_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="feestructure",
            name="batch_key",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                help_text="Groups structure rows created in one wizard save (multi-section / multi-fee batch).",
                null=True,
            ),
        ),
    ]
