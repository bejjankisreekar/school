# Generated manually: Plan billing fields, School contact/status, Coupon, SchoolSubscription

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0007_alter_school_plan_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="billing_cycle",
            field=models.CharField(
                choices=[("monthly", "Monthly"), ("yearly", "Yearly")],
                db_index=True,
                default="monthly",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="plan",
            name="is_active",
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text="Inactive plans are hidden from assignment pickers.",
            ),
        ),
        migrations.AlterField(
            model_name="plan",
            name="price_per_student",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Price per student per month (INR); for yearly cycle this is still the monthly-equivalent display rate unless you adjust manually.",
                max_digits=10,
            ),
        ),
        migrations.AddField(
            model_name="school",
            name="contact_person",
            field=models.CharField(
                blank=True,
                help_text="Primary billing or admin contact name",
                max_length=200,
            ),
        ),
        migrations.AddField(
            model_name="school",
            name="school_status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("inactive", "Inactive"),
                    ("trial", "Trial"),
                    ("suspended", "Suspended"),
                ],
                db_index=True,
                default="active",
                help_text="Lifecycle: Active, Inactive, Trial, or Suspended (syncs tenant is_active for access).",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="Coupon",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(db_index=True, max_length=40, unique=True)),
                (
                    "discount_type",
                    models.CharField(
                        choices=[("fixed", "Fixed amount (₹)"), ("percentage", "Percentage")],
                        max_length=20,
                    ),
                ),
                (
                    "discount_value",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Fixed: rupees off per bill line; Percentage: 0–100.",
                        max_digits=10,
                    ),
                ),
                (
                    "max_usage",
                    models.PositiveIntegerField(default=0, help_text="0 = unlimited redemptions."),
                ),
                ("used_count", models.PositiveIntegerField(default=0)),
                ("valid_from", models.DateField(blank=True, null=True)),
                ("valid_to", models.DateField(blank=True, null=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="SchoolSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("start_date", models.DateField()),
                ("end_date", models.DateField(blank=True, null=True)),
                (
                    "students_count",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Billable student headcount snapshot when assigned.",
                    ),
                ),
                ("free_months_applied", models.PositiveSmallIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("expired", "Expired"),
                            ("trial", "Trial"),
                        ],
                        db_index=True,
                        default="active",
                        max_length=20,
                    ),
                ),
                (
                    "is_current",
                    models.BooleanField(
                        db_index=True,
                        default=False,
                        help_text="At most one current row per school.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "coupon",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="subscription_uses",
                        to="customers.coupon",
                    ),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="school_subscriptions",
                        to="customers.plan",
                    ),
                ),
                (
                    "school",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscription_records",
                        to="customers.school",
                    ),
                ),
            ],
            options={
                "ordering": ["-start_date", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="schoolsubscription",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_current=True),
                fields=("school",),
                name="customers_schoolsub_unique_current_school",
            ),
        ),
    ]
