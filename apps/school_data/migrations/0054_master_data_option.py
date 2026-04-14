from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("school_data", "0053_academicyear_description_wizard_settings"),
    ]

    operations = [
        migrations.CreateModel(
            name="MasterDataOption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "created_on",
                    models.DateTimeField(auto_now_add=True, db_index=True, editable=False),
                ),
                ("modified_on", models.DateTimeField(auto_now=True, editable=False)),
                (
                    "key",
                    models.CharField(
                        choices=[
                            ("gender", "Gender"),
                            ("blood_group", "Blood group"),
                            ("nationality", "Nationality"),
                            ("religion", "Religion"),
                            ("mother_tongue", "Mother tongue"),
                            ("designation", "Designation"),
                            ("department", "Department"),
                            ("qualification", "Qualification"),
                        ],
                        db_index=True,
                        max_length=40,
                    ),
                ),
                ("name", models.CharField(db_index=True, max_length=160)),
                (
                    "name_normalized",
                    models.CharField(
                        db_index=True,
                        help_text="Lowercased + trimmed for duplicate prevention.",
                        max_length=180,
                    ),
                ),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="masterdataoption_created",
                        to="accounts.user",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="masterdataoption_modified",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "ordering": ["key", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="masterdataoption",
            constraint=models.UniqueConstraint(
                fields=("key", "name_normalized"),
                name="uniq_masterdata_key_name_norm",
            ),
        ),
    ]

