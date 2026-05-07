from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0028_tier_feature_catalog"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="is_archived",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="When True, the school is hidden from normal operations and tenant login is blocked; data is kept.",
            ),
        ),
    ]
