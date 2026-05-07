from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("super_admin", "0001_initial"),
        ("customers", "0017_school_platform_control_meta"),
    ]

    operations = [
        # Remove legacy per-school feature override and tier plan link from School.
        migrations.RemoveField(model_name="school", name="enabled_features_override"),
        migrations.RemoveField(model_name="school", name="saas_plan"),
        # Rename legacy billing plan field so we can use `School.plan` for SaaS plan.
        migrations.RenameField(model_name="school", old_name="plan", new_name="billing_plan"),
        # Add new SaaS plan FK (Basic/Pro/Premium) owned by apps.super_admin.
        migrations.AddField(
            model_name="school",
            name="plan",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="schools",
                to="super_admin.plan",
            ),
        ),
    ]

