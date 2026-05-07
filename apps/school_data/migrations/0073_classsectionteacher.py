# Generated manually on 2026-04-27

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0072_studentannouncement_alter_classroom_options_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClassSectionTeacher",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "class_obj",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="class_section_teachers",
                        to="school_data.classroom",
                    ),
                ),
                (
                    "section",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="class_section_teachers",
                        to="school_data.section",
                    ),
                ),
                (
                    "teacher",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="homeroom_assignments",
                        to="school_data.teacher",
                    ),
                ),
            ],
            options={
                "ordering": ["class_obj_id", "section_id"],
            },
        ),
        migrations.AddConstraint(
            model_name="classsectionteacher",
            constraint=models.UniqueConstraint(fields=("class_obj", "section"), name="uniq_class_section_homeroom_teacher"),
        ),
    ]

