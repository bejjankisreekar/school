# Generated manually for HolidayCalendar / HolidayEvent / WorkingSundayOverride

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("school_data", "0050_homework_enterprise_columns_one_by_one"),
    ]

    operations = [
        migrations.CreateModel(
            name="HolidayCalendar",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_on", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_on", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(default="Official holiday calendar", max_length=120)),
                ("is_published", models.BooleanField(db_index=True, default=False)),
                (
                    "use_split_calendars",
                    models.BooleanField(
                        default=False,
                        help_text="Use separate student vs teacher portal views; events still use “Applies to”.",
                    ),
                ),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("unpublished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "academic_year",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="holiday_calendar",
                        to="school_data.academicyear",
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
            options={
                "ordering": ["-academic_year__start_date", "id"],
            },
        ),
        migrations.CreateModel(
            name="HolidayEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_on", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_on", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=200)),
                (
                    "holiday_type",
                    models.CharField(
                        choices=[
                            ("NATIONAL", "National Holiday"),
                            ("FESTIVAL", "Festival Holiday"),
                            ("SCHOOL", "School Holiday"),
                            ("EMERGENCY", "Emergency Closure"),
                            ("EXAM_LEAVE", "Exam Leave"),
                            ("VACATION", "Vacation"),
                            ("SPECIAL", "Special Holiday"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                ("start_date", models.DateField(db_index=True)),
                ("end_date", models.DateField(db_index=True)),
                (
                    "applies_to",
                    models.CharField(
                        choices=[
                            ("STUDENTS", "Students only"),
                            ("TEACHERS", "Teachers only"),
                            ("BOTH", "Students and teachers"),
                        ],
                        db_index=True,
                        default="BOTH",
                        max_length=16,
                    ),
                ),
                ("description", models.TextField(blank=True)),
                (
                    "recurring_yearly",
                    models.BooleanField(
                        default=False,
                        help_text="If set, only single-day events (start_date = end_date) repeat on that month/day each year within the academic year.",
                    ),
                ),
                (
                    "calendar",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="school_data.holidaycalendar",
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
            options={
                "ordering": ["start_date", "name"],
            },
        ),
        migrations.CreateModel(
            name="WorkingSundayOverride",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_on", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_on", models.DateTimeField(auto_now=True)),
                ("work_date", models.DateField(db_index=True)),
                (
                    "applies_to",
                    models.CharField(
                        choices=[
                            ("STUDENTS", "Students only"),
                            ("TEACHERS", "Teachers only"),
                            ("BOTH", "Students and teachers"),
                        ],
                        default="BOTH",
                        max_length=16,
                    ),
                ),
                ("note", models.CharField(blank=True, max_length=200)),
                (
                    "calendar",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="working_sunday_overrides",
                        to="school_data.holidaycalendar",
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
            options={
                "ordering": ["work_date"],
            },
        ),
        migrations.AddConstraint(
            model_name="workingsundayoverride",
            constraint=models.UniqueConstraint(
                fields=("calendar", "work_date", "applies_to"),
                name="school_working_sunday_unique_cal_date_audience",
            ),
        ),
    ]
