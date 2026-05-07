# Generated manually on 2026-04-27

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0073_classsectionteacher"),
    ]

    operations = [
        migrations.CreateModel(
            name="TeacherClassSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "classroom",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="teacher_section_assignments",
                        to="school_data.classroom",
                    ),
                ),
                (
                    "section",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="teacher_section_assignments",
                        to="school_data.section",
                    ),
                ),
                (
                    "teacher",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="class_section_assignments",
                        to="school_data.teacher",
                    ),
                ),
            ],
            options={"ordering": ["teacher_id", "classroom_id", "section_id"]},
        ),
        migrations.AddConstraint(
            model_name="teacherclasssection",
            constraint=models.UniqueConstraint(
                fields=("teacher", "classroom", "section"),
                name="uniq_teacher_class_section",
            ),
        ),
    ]

