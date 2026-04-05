# Generated manually for optional report code and description on salary components.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0001_payroll_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="salarycomponent",
            name="code",
            field=models.CharField(
                blank=True,
                help_text="Short label for reports (e.g. HRA, PF). Optional.",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="salarycomponent",
            name="description",
            field=models.TextField(
                blank=True,
                help_text="Internal notes: policy, eligibility, or accounting mapping.",
            ),
        ),
    ]
