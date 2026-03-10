# Generated for Staff Attendance - add HOLIDAY and OTHER status

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0003_add_pro_plan_features"),
    ]

    operations = [
        migrations.AlterField(
            model_name="staffattendance",
            name="status",
            field=models.CharField(
                choices=[
                    ("PRESENT", "Present"),
                    ("ABSENT", "Absent"),
                    ("LEAVE", "Leave"),
                    ("HALF_DAY", "Half Day"),
                    ("HOLIDAY", "Holiday"),
                    ("OTHER", "Other"),
                ],
                max_length=20,
            ),
        ),
    ]
