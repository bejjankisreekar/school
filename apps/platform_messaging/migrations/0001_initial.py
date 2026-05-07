# Generated manually for platform messaging (Super Admin <-> School Admin).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("customers", "0026_saasplatformpayment_school_generated_invoice"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformMessageThread",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("pinned_at", models.DateTimeField(blank=True, null=True)),
                ("archived_by_superadmin", models.BooleanField(default=False)),
                ("archived_by_school", models.BooleanField(default=False)),
                (
                    "school",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="platform_message_thread",
                        to="customers.school",
                    ),
                ),
            ],
            options={"ordering": ["-pinned_at", "-updated_at"]},
        ),
        migrations.CreateModel(
            name="PlatformMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "sender_role",
                    models.CharField(
                        choices=[("superadmin", "Super Admin"), ("schooladmin", "School Admin")],
                        max_length=16,
                    ),
                ),
                ("body", models.TextField()),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "sender",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="platform_messages_sent",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "thread",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="messages",
                        to="platform_messaging.platformmessagethread",
                    ),
                ),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
