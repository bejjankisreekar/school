from django.db import migrations, models
import django.db.models.deletion


def backfill_year_and_enrollment(apps, schema_editor):
    AcademicYear = apps.get_model("school_data", "AcademicYear")
    Student = apps.get_model("school_data", "Student")
    StudentEnrollment = apps.get_model("school_data", "StudentEnrollment")
    Exam = apps.get_model("school_data", "Exam")
    Attendance = apps.get_model("school_data", "Attendance")
    Fee = apps.get_model("school_data", "Fee")

    active_year = AcademicYear.objects.filter(is_active=True).order_by("-start_date").first()

    for s in Student.objects.select_related("classroom__academic_year").all():
        ay = None
        if s.classroom_id and getattr(s.classroom, "academic_year_id", None):
            ay = s.classroom.academic_year
        elif active_year:
            ay = active_year
        if ay and not s.academic_year_id:
            s.academic_year_id = ay.id
            s.save(update_fields=["academic_year"])
        if ay:
            StudentEnrollment.objects.get_or_create(
                student_id=s.id,
                academic_year_id=ay.id,
                defaults={
                    "classroom_id": s.classroom_id,
                    "section_id": s.section_id,
                    "status": "ACTIVE",
                    "is_current": True,
                },
            )

    # best-effort year mapping for historical records
    for ex in Exam.objects.select_related("classroom__academic_year").all():
        if ex.academic_year_id:
            continue
        ay = None
        if ex.classroom_id and getattr(ex.classroom, "academic_year_id", None):
            ay = ex.classroom.academic_year
        elif active_year:
            ay = active_year
        if ay:
            ex.academic_year_id = ay.id
            ex.save(update_fields=["academic_year"])

    for att in Attendance.objects.select_related("student__academic_year").all():
        if att.academic_year_id:
            continue
        ay_id = getattr(att.student, "academic_year_id", None) or (active_year.id if active_year else None)
        if ay_id:
            att.academic_year_id = ay_id
            att.save(update_fields=["academic_year"])

    for fee in Fee.objects.select_related("fee_structure__academic_year", "student__academic_year").all():
        if fee.academic_year_id:
            continue
        ay_id = (
            getattr(fee.fee_structure, "academic_year_id", None)
            or getattr(fee.student, "academic_year_id", None)
            or (active_year.id if active_year else None)
        )
        if ay_id:
            fee.academic_year_id = ay_id
            fee.save(update_fields=["academic_year"])


class Migration(migrations.Migration):
    dependencies = [
        ("school_data", "0022_exam_session"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="academic_year",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="students",
                to="school_data.academicyear",
            ),
        ),
        migrations.AddField(
            model_name="exam",
            name="academic_year",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="exams",
                to="school_data.academicyear",
            ),
        ),
        migrations.AddField(
            model_name="attendance",
            name="academic_year",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="attendance_records",
                to="school_data.academicyear",
            ),
        ),
        migrations.AddField(
            model_name="fee",
            name="academic_year",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="fees",
                to="school_data.academicyear",
            ),
        ),
        migrations.CreateModel(
            name="StudentEnrollment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("ACTIVE", "Active"), ("PROMOTED", "Promoted"), ("DEMOTED", "Demoted"), ("TRANSFERRED", "Transferred"), ("DETAINED", "Detained")], default="ACTIVE", max_length=20)),
                ("is_current", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("academic_year", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="enrollments", to="school_data.academicyear")),
                ("classroom", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="enrollments", to="school_data.classroom")),
                ("section", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="enrollments", to="school_data.section")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="enrollments", to="school_data.student")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="StudentPromotion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[("PROMOTE", "Promote"), ("DEMOTE", "Demote"), ("TRANSFER", "Transfer"), ("DETAIN", "Detain")], max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="student_promotions_created", to="accounts.user")),
                ("from_class", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="promotions_from", to="school_data.classroom")),
                ("from_section", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="promotions_from_section", to="school_data.section")),
                ("from_year", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="promotions_from_year", to="school_data.academicyear")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="promotions", to="school_data.student")),
                ("to_class", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="promotions_to", to="school_data.classroom")),
                ("to_section", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="promotions_to_section", to="school_data.section")),
                ("to_year", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="promotions_to_year", to="school_data.academicyear")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="studentenrollment",
            constraint=models.UniqueConstraint(fields=("student", "academic_year"), name="unique_student_enrollment_per_year"),
        ),
        migrations.AddConstraint(
            model_name="studentenrollment",
            constraint=models.UniqueConstraint(condition=models.Q(("is_current", True)), fields=("student",), name="unique_current_enrollment_per_student"),
        ),
        migrations.AddConstraint(
            model_name="studentpromotion",
            constraint=models.UniqueConstraint(fields=("student", "from_year", "to_year"), name="unique_student_promotion_year_pair"),
        ),
        migrations.RunPython(backfill_year_and_enrollment, migrations.RunPython.noop),
    ]

