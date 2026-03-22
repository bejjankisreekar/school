from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0009_exam_teacher"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attendance",
            name="status",
            field=models.CharField(
                choices=[
                    ("PRESENT", "Present"),
                    ("ABSENT", "Absent"),
                    ("LEAVE", "Leave"),
                ],
                max_length=10,
            ),
        ),
    ]

