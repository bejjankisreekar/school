from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_schoolenrollmentrequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="address",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="branch_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="city",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="institution_code",
            field=models.CharField(
                blank=True,
                help_text="Short code or abbreviation for the school (optional).",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="intended_plan",
            field=models.CharField(
                blank=True,
                help_text="trial, monthly, or yearly — onboarding preference only.",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="pending_password_hash",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="pincode",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="preferred_username",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="state",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="student_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="teacher_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
