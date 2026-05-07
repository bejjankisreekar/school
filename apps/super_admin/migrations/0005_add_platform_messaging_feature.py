from django.db import migrations


def forwards(apps, schema_editor):
    Feature = apps.get_model("super_admin", "Feature")
    Plan = apps.get_model("super_admin", "Plan")
    feat, _ = Feature.objects.update_or_create(
        code="platform_messaging",
        defaults={
            "name": "Platform messaging",
            "category": "communication",
        },
    )
    for plan in Plan.objects.filter(features__code="messaging").distinct():
        plan.features.add(feat)


def backwards(apps, schema_editor):
    Feature = apps.get_model("super_admin", "Feature")
    Feature.objects.filter(code="platform_messaging").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("super_admin", "0004_control_center_settings"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
