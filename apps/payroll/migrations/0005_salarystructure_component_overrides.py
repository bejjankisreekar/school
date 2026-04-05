# Per-employee % / fixed overrides via explicit through model.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0004_salarystructure_applicable_components"),
    ]

    operations = [
        migrations.RunSQL(
            """
            ALTER TABLE payroll_salarystructure_applicable_components
              ADD COLUMN IF NOT EXISTS use_component_default boolean NOT NULL DEFAULT true;
            ALTER TABLE payroll_salarystructure_applicable_components
              ADD COLUMN IF NOT EXISTS override_calculation_type varchar(20) NOT NULL DEFAULT '';
            ALTER TABLE payroll_salarystructure_applicable_components
              ADD COLUMN IF NOT EXISTS override_value numeric(12,2) NULL;
            """,
            migrations.RunSQL.noop,
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="salarystructure",
                    name="applicable_components",
                ),
                migrations.CreateModel(
                    name="SalaryStructureComponent",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "use_component_default",
                            models.BooleanField(
                                default=True,
                                help_text="If True, use master component rate. If False, use override fields below.",
                            ),
                        ),
                        (
                            "override_calculation_type",
                            models.CharField(
                                blank=True,
                                choices=[
                                    ("PERCENTAGE", "Percentage of Basic"),
                                    ("FIXED", "Fixed Amount"),
                                ],
                                default="",
                                max_length=20,
                            ),
                        ),
                        (
                            "override_value",
                            models.DecimalField(
                                blank=True,
                                decimal_places=2,
                                max_digits=12,
                                null=True,
                            ),
                        ),
                        (
                            "component",
                            models.ForeignKey(
                                db_column="salarycomponent_id",
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="structure_links",
                                to="payroll.salarycomponent",
                            ),
                        ),
                        (
                            "salary_structure",
                            models.ForeignKey(
                                db_column="salarystructure_id",
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="component_links",
                                to="payroll.salarystructure",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "payroll_salarystructure_applicable_components",
                        "unique_together": {("salary_structure", "component")},
                    },
                ),
                migrations.AddField(
                    model_name="salarystructure",
                    name="applicable_components",
                    field=models.ManyToManyField(
                        blank=True,
                        help_text="Links when not using default list, plus optional per-employee rate overrides.",
                        related_name="salary_structures",
                        through="payroll.salarystructurecomponent",
                        to="payroll.salarycomponent",
                    ),
                ),
            ],
        ),
    ]
