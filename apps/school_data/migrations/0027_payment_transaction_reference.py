from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0026_exam_marks_lock_column_safety"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="transaction_reference",
            field=models.CharField(
                blank=True,
                help_text="UPI ref., bank ref., or online payment id (optional).",
                max_length=120,
            ),
        ),
        migrations.AlterField(
            model_name="payment",
            name="receipt_number",
            field=models.CharField(
                blank=True,
                help_text="School receipt / voucher number (optional).",
                max_length=50,
            ),
        ),
    ]
