from django.db import migrations


def forwards(apps, schema_editor):
    SidebarMenuItem = apps.get_model("core", "SidebarMenuItem")
    SidebarMenuItem.objects.get_or_create(
        role="ADMIN",
        route_name="core:school_admin_platform_messages",
        defaults={
            "label": "Messages to platform",
            "icon": "bi bi-building-check",
            "display_order": 23,
            "feature_code": "platform_messaging",
            "href": "",
            "parent_id": None,
            "is_visible": True,
            "is_active": True,
        },
    )


def backwards(apps, schema_editor):
    SidebarMenuItem = apps.get_model("core", "SidebarMenuItem")
    SidebarMenuItem.objects.filter(
        role="ADMIN",
        route_name="core:school_admin_platform_messages",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0024_schoolenrollmentrequest_society_name"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
