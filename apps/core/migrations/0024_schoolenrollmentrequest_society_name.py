from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_seed_default_analytics_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="society_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Housing society, trust, or registered name as on records.",
                max_length=255,
            ),
        ),
    ]
