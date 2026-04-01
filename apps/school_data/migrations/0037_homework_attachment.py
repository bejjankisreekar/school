from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0036_feetype_description_active"),
    ]

    operations = [
        migrations.AddField(
            model_name="homework",
            name="attachment",
            field=models.FileField(
                blank=True,
                help_text="Optional worksheet or reference file for students.",
                null=True,
                upload_to="homework_attachments/%Y/%m/",
            ),
        ),
    ]
