from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0013_subject_master_remove_class_teacher_year"),
    ]

    operations = [
        migrations.AddField(
            model_name="exam",
            name="subject",
            field=models.ForeignKey(
                blank=True,
                help_text="When set, this exam is for one subject (single / scheduled exams).",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="exams",
                to="school_data.subject",
            ),
        ),
        migrations.AddField(
            model_name="exam",
            name="total_marks",
            field=models.PositiveIntegerField(
                blank=True,
                default=100,
                help_text="Default max marks when teachers enter scores.",
                null=True,
            ),
        ),
    ]
