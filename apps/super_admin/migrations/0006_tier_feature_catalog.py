from django.db import migrations


def forwards(apps, schema_editor):
    from apps.core.plan_features import seed_super_admin_tier_features

    seed_super_admin_tier_features()


class Migration(migrations.Migration):
    dependencies = [
        ("super_admin", "0005_add_platform_messaging_feature"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
