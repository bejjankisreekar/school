# Generated manually for exam_date field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="marks",
            name="exam_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
