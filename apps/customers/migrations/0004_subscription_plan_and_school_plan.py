# Generated for subscription plan system

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0003_add_pro_plan_features"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubscriptionPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(choices=[("trial", "Trial"), ("basic", "Basic"), ("pro", "Pro")], max_length=50, unique=True)),
                ("price_per_student", models.DecimalField(decimal_places=2, default=0, help_text="Price per student per year. Basic=39, Pro=59, Trial=0", max_digits=10)),
                ("duration_days", models.IntegerField(default=365, help_text="Trial: 14, Basic/Pro: 365")),
                ("is_active", models.BooleanField(db_index=True, default=True)),
            ],
            options={
                "ordering": ["price_per_student"],
                "verbose_name": "Subscription Plan",
                "verbose_name_plural": "Subscription Plans",
            },
        ),
        migrations.AddField(
            model_name="school",
            name="plan",
            field=models.ForeignKey(
                blank=True,
                help_text="Trial, Basic, or Pro",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="schools",
                to="customers.subscriptionplan",
            ),
        ),
        migrations.AddField(
            model_name="school",
            name="trial_end_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
