from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("school_data", "0058_admission_number_remove_class_part"),
    ]

    operations = [
        migrations.AddField(
            model_name="masterdataoption",
            name="display_order",
            field=models.PositiveIntegerField(db_index=True, default=0),
        ),
    ]

