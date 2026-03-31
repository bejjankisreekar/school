from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0030_student_extra_data"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="academicyear",
            name="unique_active_academic_year",
        ),
    ]
