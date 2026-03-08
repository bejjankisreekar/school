# Add BaseModel audit fields to School, AcademicYear, ClassRoom, Section, Subject, Teacher, Student

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_school_fk_to_code"),
        ("core", "0009_school_fk_to_code"),
    ]

    operations = [
        # School: remove created_at, add audit fields
        migrations.RemoveField(model_name="school", name="created_at"),
        migrations.AddField(
            model_name="school",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="school_created",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="school",
            name="created_on",
            field=models.DateTimeField(auto_now_add=True, default=timezone.now, editable=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="school",
            name="modified_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="school_modified",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="school",
            name="modified_on",
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
        # AcademicYear
        migrations.AddField(
            model_name="academicyear",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="academicyear_created",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="academicyear",
            name="created_on",
            field=models.DateTimeField(auto_now_add=True, default=timezone.now, editable=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="academicyear",
            name="modified_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="academicyear_modified",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="academicyear",
            name="modified_on",
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
        # ClassRoom
        migrations.AddField(
            model_name="classroom",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="classroom_created",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="classroom",
            name="created_on",
            field=models.DateTimeField(auto_now_add=True, default=timezone.now, editable=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="classroom",
            name="modified_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="classroom_modified",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="classroom",
            name="modified_on",
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
        # Section
        migrations.AddField(
            model_name="section",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="section_created",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="section",
            name="created_on",
            field=models.DateTimeField(auto_now_add=True, default=timezone.now, editable=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="section",
            name="modified_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="section_modified",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="section",
            name="modified_on",
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
        # Subject
        migrations.AddField(
            model_name="subject",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="subject_created",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="subject",
            name="created_on",
            field=models.DateTimeField(auto_now_add=True, default=timezone.now, editable=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="subject",
            name="modified_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="subject_modified",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="subject",
            name="modified_on",
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
        # Teacher
        migrations.AddField(
            model_name="teacher",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="teacher_created",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="teacher",
            name="created_on",
            field=models.DateTimeField(auto_now_add=True, default=timezone.now, editable=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="teacher",
            name="modified_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="teacher_modified",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="teacher",
            name="modified_on",
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
        # Student
        migrations.AddField(
            model_name="student",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="student_created",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="student",
            name="created_on",
            field=models.DateTimeField(auto_now_add=True, default=timezone.now, editable=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="student",
            name="modified_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="student_modified",
                to=settings.AUTH_USER_MODEL,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="student",
            name="modified_on",
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
    ]
