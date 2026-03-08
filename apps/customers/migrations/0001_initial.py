# Generated for django-tenants multi-tenancy

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("core", "0013_basic_plan_features"),
    ]

    operations = [
        migrations.CreateModel(
            name="School",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("schema_name", models.CharField(db_index=True, max_length=63, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("code", models.CharField(help_text="Unique school code e.g. school_001", max_length=50, unique=True)),
                ("created_on", models.DateTimeField(auto_now_add=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("address", models.TextField(blank=True)),
                ("contact_email", models.EmailField(blank=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=20)),
                ("auto_create_schema", models.BooleanField(default=True)),
                ("auto_drop_schema", models.BooleanField(default=False)),
                (
                    "subscription_plan",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="schools",
                        to="core.plan",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Domain",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("domain", models.CharField(db_index=True, max_length=253, unique=True)),
                ("is_primary", models.BooleanField(db_index=True, default=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="domains",
                        to="customers.school",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="PlatformSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=100, unique=True)),
                ("value", models.JSONField(blank=True, default=dict)),
                ("updated_on", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
