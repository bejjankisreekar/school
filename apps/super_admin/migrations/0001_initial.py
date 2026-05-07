from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Feature",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("code", models.CharField(db_index=True, max_length=100, unique=True)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("academic", "Academic"),
                            ("operations", "Operations"),
                            ("exams", "Exams"),
                            ("communication", "Communication"),
                            ("finance", "Finance"),
                        ],
                        db_index=True,
                        default="academic",
                        max_length=32,
                    ),
                ),
            ],
            options={"ordering": ["category", "name"]},
        ),
        migrations.CreateModel(
            name="Plan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "name",
                    models.CharField(
                        choices=[("basic", "Basic"), ("pro", "Pro"), ("premium", "Premium")],
                        db_index=True,
                        max_length=20,
                        unique=True,
                    ),
                ),
                ("price", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddField(
            model_name="plan",
            name="features",
            field=models.ManyToManyField(blank=True, related_name="plans", to="super_admin.feature"),
        ),
    ]

