# Generated manually for bulk attendance

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0002_add_exam_date_to_marks"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClassRoom",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=20)),
                ("section", models.CharField(max_length=10)),
                ("school", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="classrooms", to="core.school")),
            ],
            options={
                "unique_together": {("name", "section", "school")},
            },
        ),
        migrations.AddField(
            model_name="student",
            name="classroom",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="students", to="core.classroom"),
        ),
        migrations.AddField(
            model_name="attendance",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="attendance",
            name="marked_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="marked_attendance", to=settings.AUTH_USER_MODEL),
        ),
    ]
