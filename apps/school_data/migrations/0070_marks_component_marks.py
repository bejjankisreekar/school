from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0069_paymentbatch_receipt_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="marks",
            name="component_marks",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

