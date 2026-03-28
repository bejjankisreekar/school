# ClassSectionSubjectTeacher was missing from historical migrations; create table now.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0017_exam_start_end_time"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClassSectionSubjectTeacher",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_on",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                ("modified_on", models.DateTimeField(auto_now=True)),
                (
                    "class_obj",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="class_section_subject_teacher_mappings",
                        to="school_data.classroom",
                    ),
                ),
                (
                    "section",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="class_section_subject_teacher_mappings",
                        to="school_data.section",
                    ),
                ),
                (
                    "subject",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="class_section_subject_teacher_mappings",
                        to="school_data.subject",
                    ),
                ),
                (
                    "teacher",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="class_section_subject_teacher_mappings",
                        to="school_data.teacher",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created",
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
                        related_name="%(class)s_modified",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="classsectionsubjectteacher",
            constraint=models.UniqueConstraint(
                fields=("class_obj", "section", "subject"),
                name="unique_class_section_subject_teacher",
            ),
        ),
    ]
