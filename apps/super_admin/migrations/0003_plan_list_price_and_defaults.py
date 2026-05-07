from decimal import Decimal

from django.db import migrations, models


def set_prices(apps, schema_editor):
    Plan = apps.get_model("super_admin", "Plan")

    # discounted, list
    mapping = {
        "basic": (Decimal("49.00"), Decimal("59.00")),
        "pro": (Decimal("79.00"), Decimal("89.00")),
        "premium": (Decimal("89.00"), Decimal("99.00")),
    }
    for key, (price, list_price) in mapping.items():
        Plan.objects.filter(name=key).update(price=price, list_price=list_price)


class Migration(migrations.Migration):
    dependencies = [
        ("super_admin", "0002_seed_features_and_plans"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="list_price",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.RunPython(set_prices, migrations.RunPython.noop),
    ]

