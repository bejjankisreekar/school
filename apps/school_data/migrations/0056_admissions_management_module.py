from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("school_data", "0055_seed_master_data_defaults"),
    ]

    operations = [
        migrations.CreateModel(
            name="Admission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_on", models.DateTimeField(auto_now_add=True, db_index=True, editable=False)),
                ("modified_on", models.DateTimeField(auto_now=True, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="admission_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="admission_modified",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "application_id",
                    models.CharField(
                        db_index=True,
                        help_text="Auto generated like ADM-2026-0001",
                        max_length=32,
                        unique=True,
                    ),
                ),
                ("admission_date", models.DateField(blank=True, db_index=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("NEW", "New"),
                            ("UNDER_REVIEW", "Under review"),
                            ("DOCUMENT_PENDING", "Document pending"),
                            ("APPROVED", "Approved"),
                            ("REJECTED", "Rejected"),
                            ("JOINED", "Joined"),
                        ],
                        db_index=True,
                        default="NEW",
                        max_length=32,
                    ),
                ),
                ("first_name", models.CharField(max_length=150)),
                ("last_name", models.CharField(blank=True, default="", max_length=150)),
                ("gender", models.CharField(blank=True, default="", max_length=60)),
                ("date_of_birth", models.DateField(blank=True, null=True)),
                ("blood_group", models.CharField(blank=True, default="", max_length=20)),
                ("aadhaar_or_student_id", models.CharField(blank=True, db_index=True, default="", max_length=32)),
                ("passport_photo", models.ImageField(blank=True, null=True, upload_to="admissions/photos/%Y/%m/")),
                ("father_name", models.CharField(blank=True, default="", max_length=150)),
                ("mother_name", models.CharField(blank=True, default="", max_length=150)),
                ("mobile_number", models.CharField(blank=True, db_index=True, default="", max_length=20)),
                ("alternate_mobile", models.CharField(blank=True, default="", max_length=20)),
                ("email", models.EmailField(blank=True, default="", max_length=254)),
                ("occupation", models.CharField(blank=True, default="", max_length=120)),
                ("annual_income", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("house_no", models.CharField(blank=True, default="", max_length=80)),
                ("street", models.CharField(blank=True, default="", max_length=180)),
                ("city", models.CharField(blank=True, default="", max_length=120)),
                ("state", models.CharField(blank=True, default="", max_length=120)),
                ("pincode", models.CharField(blank=True, default="", max_length=12)),
                ("previous_school_name", models.CharField(blank=True, default="", max_length=200)),
                ("previous_marks_percent", models.CharField(blank=True, default="", max_length=50)),
                ("require_bus", models.BooleanField(default=False)),
                ("pickup_point", models.CharField(blank=True, default="", max_length=160)),
                ("notes", models.TextField(blank=True, default="")),
                (
                    "applying_for_class",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="admissions_applications",
                        to="school_data.classroom",
                    ),
                ),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="admissions_approved",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "rejected_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="admissions_rejected",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_student",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_from_admissions",
                        to="school_data.student",
                    ),
                ),
            ],
            options={"ordering": ["-created_on"]},
        ),
        migrations.CreateModel(
            name="AdmissionDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "doc_type",
                    models.CharField(
                        choices=[
                            ("TRANSFER_CERT", "Transfer certificate"),
                            ("BIRTH_CERT", "Birth certificate"),
                            ("OTHER", "Other"),
                        ],
                        db_index=True,
                        default="OTHER",
                        max_length=40,
                    ),
                ),
                ("title", models.CharField(blank=True, default="", max_length=200)),
                ("file", models.FileField(upload_to="admissions/docs/%Y/%m/")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "admission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="documents",
                        to="school_data.admission",
                    ),
                ),
            ],
            options={"ordering": ["-uploaded_at"]},
        ),
        migrations.CreateModel(
            name="AdmissionStatusHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_on", models.DateTimeField(auto_now_add=True, db_index=True, editable=False)),
                ("modified_on", models.DateTimeField(auto_now=True, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="admissionstatushistory_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="admissionstatushistory_modified",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("from_status", models.CharField(blank=True, default="", max_length=32)),
                ("to_status", models.CharField(db_index=True, max_length=32)),
                ("note", models.CharField(blank=True, default="", max_length=240)),
                (
                    "admission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="status_history",
                        to="school_data.admission",
                    ),
                ),
            ],
            options={"ordering": ["-created_on", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="admission",
            constraint=models.UniqueConstraint(
                condition=models.Q(("aadhaar_or_student_id__gt", "")),
                fields=("aadhaar_or_student_id",),
                name="uniq_admission_aadhaar_per_tenant",
            ),
        ),
    ]

