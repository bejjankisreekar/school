# Generated manually for subscription plan system

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_pro_plan_features"),
        ("customers", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubscriptionPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("price", models.DecimalField(decimal_places=2, help_text="Price in INR", max_digits=12)),
                ("billing_cycle", models.CharField(
                    choices=[("MONTHLY", "Monthly"), ("QUARTERLY", "Quarterly"), ("YEARLY", "Yearly")],
                    default="YEARLY",
                    max_length=20,
                )),
                ("description", models.TextField(blank=True)),
                ("features", models.JSONField(
                    blank=True,
                    default=list,
                    help_text="List of feature strings, e.g. ['online_admissions', 'library']",
                )),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_on", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Subscription Plan",
                "verbose_name_plural": "Subscription Plans",
                "ordering": ["price"],
            },
        ),
        migrations.CreateModel(
            name="SchoolSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                ("is_trial", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="school_subscriptions",
                        to="core.subscriptionplan",
                    ),
                ),
                (
                    "school",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscriptions",
                        to="customers.school",
                    ),
                ),
            ],
            options={
                "verbose_name": "School Subscription",
                "verbose_name_plural": "School Subscriptions",
                "ordering": ["-start_date"],
            },
        ),
    ]
