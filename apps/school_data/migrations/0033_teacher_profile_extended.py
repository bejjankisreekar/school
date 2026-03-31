from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0032_drop_unique_active_academic_year_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="teacher",
            name="address",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="teacher",
            name="date_of_birth",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="teacher",
            name="extra_data",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Extended profile: contact, professional, family, medical, payroll, etc.",
            ),
        ),
        migrations.AddField(
            model_name="teacher",
            name="gender",
            field=models.CharField(
                blank=True,
                choices=[("M", "Male"), ("F", "Female"), ("O", "Other")],
                db_index=True,
                default="",
                max_length=1,
            ),
        ),
        migrations.AddField(
            model_name="teacher",
            name="profile_image",
            field=models.ImageField(blank=True, null=True, upload_to="teacher_profiles/"),
        ),
    ]
