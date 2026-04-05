# Per-employee optional allowances/deductions (M2M).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0003_salarycomponent_order_one_based"),
    ]

    operations = [
        migrations.AddField(
            model_name="salarystructure",
            name="use_default_salary_components",
            field=models.BooleanField(
                default=True,
                help_text="If True, every active allowance and deduction applies. If False, only linked components apply (list may be empty).",
            ),
        ),
        migrations.AddField(
            model_name="salarystructure",
            name="applicable_components",
            field=models.ManyToManyField(
                blank=True,
                help_text="Used when use_default_salary_components is False: explicit heads for this employee.",
                related_name="salary_structures",
                to="payroll.salarycomponent",
            ),
        ),
    ]
