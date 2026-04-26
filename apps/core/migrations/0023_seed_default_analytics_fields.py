from django.db import migrations


def seed_registry(apps, schema_editor):
    AnalyticsField = apps.get_model("core", "AnalyticsField")
    if AnalyticsField.objects.exists():
        return
    rows = [
        ("department", "Department", "Report filters", 10),
        ("staff_type", "Staff type", "Report filters", 20),
        ("attendance_status", "Attendance status", "Report filters", 30),
        ("admission_status", "Admission status", "Report filters", 40),
        ("academic_year", "Academic year", "Report filters", 50),
    ]
    for field_key, display_label, category, display_order in rows:
        AnalyticsField.objects.create(
            field_key=field_key,
            display_label=display_label,
            category=category,
            display_order=display_order,
            is_active=True,
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_analyticsfield"),
    ]

    operations = [
        migrations.RunPython(seed_registry, reverse_code=noop_reverse),
    ]
