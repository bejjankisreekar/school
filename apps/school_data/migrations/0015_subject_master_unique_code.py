from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0014_exam_subject_total_marks"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="subject",
            name="unique_subject_code_non_blank",
        ),
        migrations.AlterField(
            model_name="subject",
            name="code",
            field=models.CharField(
                db_index=True,
                help_text="Short unique code, e.g. MATH01.",
                max_length=50,
                unique=True,
            ),
        ),
    ]
