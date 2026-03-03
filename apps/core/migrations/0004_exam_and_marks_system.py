# Generated manually for Exam & Marks system

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0003_bulk_attendance_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="Exam",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                ("classroom", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="exams", to="core.classroom")),
                ("school", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="exams", to="core.school")),
            ],
        ),
        migrations.AddField(
            model_name="subject",
            name="classroom",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="subjects", to="core.classroom"),
        ),
        migrations.AddField(
            model_name="marks",
            name="exam",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="marks", to="core.exam"),
        ),
        migrations.AddField(
            model_name="marks",
            name="entered_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="entered_marks", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name="marks",
            name="exam_name",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddConstraint(
            model_name="marks",
            constraint=models.UniqueConstraint(condition=Q(("exam__isnull", False)), fields=("student", "subject", "exam"), name="unique_student_subject_exam"),
        ),
    ]
