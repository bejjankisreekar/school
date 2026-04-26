# Generated manually for per-subject mark components (theory/practical/etc.)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0062_master_data_option_comprehensive_defaults"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExamMarkComponent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("component_name", models.CharField(max_length=64)),
                ("max_marks", models.PositiveIntegerField()),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                (
                    "exam",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mark_components",
                        to="school_data.exam",
                    ),
                ),
            ],
            options={
                "verbose_name": "exam mark component",
                "verbose_name_plural": "exam mark components",
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="exammarkcomponent",
            constraint=models.UniqueConstraint(
                fields=("exam", "component_name"),
                name="uniq_exam_mark_component_name",
            ),
        ),
    ]
