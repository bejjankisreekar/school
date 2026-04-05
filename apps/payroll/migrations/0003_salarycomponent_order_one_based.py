# Display order is 1-based; shift existing rows and set default=1.

from django.db import migrations, models


def forwards_increment_order(apps, schema_editor):
    SalaryComponent = apps.get_model("payroll", "SalaryComponent")
    for row in SalaryComponent.objects.all().only("id", "order"):
        row.order = row.order + 1
        row.save(update_fields=["order"])


def backwards_decrement_order(apps, schema_editor):
    SalaryComponent = apps.get_model("payroll", "SalaryComponent")
    for row in SalaryComponent.objects.all().only("id", "order"):
        row.order = max(0, row.order - 1)
        row.save(update_fields=["order"])


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0002_salarycomponent_code_description"),
    ]

    operations = [
        migrations.RunPython(forwards_increment_order, backwards_decrement_order),
        migrations.AlterField(
            model_name="salarycomponent",
            name="order",
            field=models.PositiveIntegerField(
                default=1,
                help_text="1 = first in lists; lower numbers appear before higher for the same category.",
            ),
        ),
    ]
