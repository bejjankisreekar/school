from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0001_initial"),
        ("accounts", "0008_user_profile"),
    ]

    operations = [
        migrations.CreateModel(
            name="BlockedLoginAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("username", models.CharField(db_index=True, max_length=150)),
                ("role", models.CharField(blank=True, db_index=True, max_length=20)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("reason", models.CharField(db_index=True, default="inactive_account", max_length=120)),
                ("attempted_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "school",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="blocked_login_attempts",
                        to="customers.school",
                        to_field="code",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="blocked_login_attempts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-attempted_at", "-id"],
            },
        ),
    ]

