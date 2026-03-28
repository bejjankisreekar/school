from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0023_academic_year_promotion_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="gender",
            field=models.CharField(
                blank=True,
                choices=[("M", "Male"), ("F", "Female"), ("O", "Other")],
                db_index=True,
                default="",
                help_text="Used for reports and demographics (optional).",
                max_length=1,
            ),
        ),
    ]
