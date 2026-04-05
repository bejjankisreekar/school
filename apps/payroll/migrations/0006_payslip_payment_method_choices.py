from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0005_salarystructure_component_overrides"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payslip",
            name="payment_method",
            field=models.CharField(
                blank=True,
                choices=[
                    ("Bank Transfer", "Bank Transfer"),
                    ("Cash Payment", "Cash Payment"),
                    ("Cheque", "Cheque"),
                    ("UPI / Digital", "UPI / Digital"),
                ],
                default="Bank Transfer",
                max_length=50,
            ),
        ),
    ]
