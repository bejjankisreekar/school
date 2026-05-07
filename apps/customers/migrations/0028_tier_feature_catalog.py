from django.db import migrations


def forwards(apps, schema_editor):
    from apps.core.plan_features import seed_customer_tier_plans

    seed_customer_tier_plans()


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0027_schoolsubscription_is_active"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
