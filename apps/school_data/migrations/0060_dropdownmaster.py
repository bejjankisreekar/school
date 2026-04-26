from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0059_masterdata_display_order"),
    ]

    operations = [
        migrations.CreateModel(
            name="DropdownMaster",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_on", models.DateTimeField(auto_now_add=True)),
                ("updated_on", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="%(class)s_created", to="accounts.user")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="%(class)s_updated", to="accounts.user")),
                ("field_key", models.CharField(db_index=True, max_length=80)),
                ("display_label", models.CharField(db_index=True, max_length=160)),
                ("option_value", models.CharField(db_index=True, max_length=160)),
                ("option_value_normalized", models.CharField(db_index=True, help_text="Lowercased + trimmed for duplicate prevention.", max_length=180)),
                ("category", models.CharField(blank=True, db_index=True, default="", max_length=60)),
                ("display_order", models.PositiveIntegerField(db_index=True, default=0)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
            ],
            options={
                "ordering": ["category", "field_key", "display_order", "display_label"],
            },
        ),
        migrations.AddConstraint(
            model_name="dropdownmaster",
            constraint=models.UniqueConstraint(fields=("field_key", "option_value_normalized"), name="uniq_dropdownmaster_key_value_norm"),
        ),
    ]

