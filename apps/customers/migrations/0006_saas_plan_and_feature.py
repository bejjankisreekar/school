# Generated manually for SaaS Plan and Feature Management

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0005_alter_school_subscription_plan"),
    ]

    operations = [
        migrations.CreateModel(
            name="Feature",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("code", models.CharField(help_text="Unique code, e.g. students, fees, payroll", max_length=50, unique=True)),
                ("description", models.TextField(blank=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Plan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("price_per_student", models.DecimalField(decimal_places=2, default=0, help_text="Price per student per year", max_digits=10)),
                ("description", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["price_per_student"],
            },
        ),
        migrations.AddField(
            model_name="plan",
            name="features",
            field=models.ManyToManyField(blank=True, help_text="Features included in this plan", related_name="plans", to="customers.feature"),
        ),
        migrations.AddField(
            model_name="school",
            name="saas_plan",
            field=models.ForeignKey(
                blank=True,
                help_text="Starter, Growth, or Enterprise - controls available modules",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="schools",
                to="customers.plan",
            ),
        ),
        migrations.AddField(
            model_name="school",
            name="enabled_features_override",
            field=models.JSONField(
                blank=True,
                default=None,
                help_text="Optional: list of feature codes enabled for this school. If set, overrides plan defaults.",
                null=True,
            ),
        ),
    ]
