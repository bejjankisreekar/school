from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_schoolenrollmentrequest_extended"),
    ]

    operations = [
        migrations.CreateModel(
            name="SidebarMenuItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_on", models.DateTimeField(auto_now_add=True, db_index=True, editable=False)),
                ("modified_on", models.DateTimeField(auto_now=True, editable=False)),
                ("role", models.CharField(choices=[("SUPERADMIN", "Super Admin"), ("ADMIN", "School Admin"), ("TEACHER", "Teacher"), ("STUDENT", "Student"), ("PARENT", "Parent")], db_index=True, max_length=20)),
                ("label", models.CharField(max_length=80)),
                ("route_name", models.CharField(blank=True, default="", help_text="Django URL name, e.g. core:school_students_list", max_length=120)),
                ("href", models.CharField(blank=True, default="", help_text="Optional fallback URL if route_name is empty or cannot be reversed.", max_length=240)),
                ("icon", models.CharField(blank=True, default="", help_text='Bootstrap icon class, e.g. \"bi bi-people\".', max_length=60)),
                ("display_order", models.PositiveIntegerField(db_index=True, default=0)),
                ("is_visible", models.BooleanField(db_index=True, default=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("feature_code", models.CharField(blank=True, default="", help_text="Optional feature gate code (matches has_feature_access).", max_length=64)),
                ("created_by", models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sidebarmenuitem_created", to="accounts.user")),
                ("modified_by", models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sidebarmenuitem_modified", to="accounts.user")),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="children", to="core.sidebarmenuitem")),
            ],
            options={
                "ordering": ["role", "parent_id", "display_order", "id"],
                "indexes": [models.Index(fields=["role", "parent", "display_order"], name="core_sideb_role_9b6a49_idx")],
            },
        ),
    ]

