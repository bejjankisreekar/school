from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0010_attendance_leave_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="HomeworkSubmission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("submission_file", models.FileField(blank=True, null=True, upload_to="homework_submissions/%Y/%m/")),
                (
                    "status",
                    models.CharField(
                        choices=[("PENDING", "Pending"), ("COMPLETED", "Completed")],
                        db_index=True,
                        default="PENDING",
                        max_length=10,
                    ),
                ),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("remarks", models.TextField(blank=True)),
                (
                    "homework",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="submissions",
                        to="school_data.homework",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="homework_submissions",
                        to="school_data.student",
                    ),
                ),
            ],
            options={
                "ordering": ["-submitted_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="homeworksubmission",
            constraint=models.UniqueConstraint(
                fields=("homework", "student"),
                name="unique_homework_submission_per_student",
            ),
        ),
    ]

