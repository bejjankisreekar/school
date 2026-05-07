# Generated manually for optional /enroll/ extended profile fields.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_sidebar_platform_messaging_item"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="website",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="affiliation_board",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="school_type",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="established_year",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="school_motto",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="affiliation_number",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="landmark",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="district",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="latitude",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="longitude",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="maps_url",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="alternate_contact_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="alternate_contact_phone",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="admin_designation",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="admin_profile_photo",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="enrollment/admin_photos/%Y/%m/",
            ),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="instruction_medium",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="classes_offered",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="streams_offered",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="sections_per_class_notes",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="curriculum_type",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="total_classrooms",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="lab_physics",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="lab_chemistry",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="lab_computer",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="has_library",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="has_playground",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="has_transport",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="total_student_capacity",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="current_student_strength",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="non_teaching_staff_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="uses_erp",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="current_erp_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="require_data_migration",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="preferred_ui_language",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="expected_start_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="detailed_requirements",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="school_logo",
            field=models.ImageField(blank=True, null=True, upload_to="enrollment/logos/%Y/%m/"),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="registration_certificate",
            field=models.FileField(blank=True, null=True, upload_to="enrollment/certificates/%Y/%m/"),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="address_proof",
            field=models.FileField(blank=True, null=True, upload_to="enrollment/address_proof/%Y/%m/"),
        ),
        migrations.AddField(
            model_name="schoolenrollmentrequest",
            name="other_documents",
            field=models.FileField(blank=True, null=True, upload_to="enrollment/other/%Y/%m/"),
        ),
    ]
