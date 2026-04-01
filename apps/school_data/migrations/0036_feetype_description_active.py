from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0035_fee_concession_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="feetype",
            name="description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="feetype",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
    ]
