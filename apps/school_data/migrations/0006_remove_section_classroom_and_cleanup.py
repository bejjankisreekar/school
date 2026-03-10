# Remove Section.classroom, class_teacher, capacity; update Student constraint

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0005_refactor_section_class_structure"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="classroom",
            name="section",
        ),
        migrations.RemoveConstraint(
            model_name="student",
            name="unique_roll_per_section",
        ),
        migrations.RemoveField(
            model_name="section",
            name="class_teacher",
        ),
        migrations.RemoveField(
            model_name="section",
            name="classroom",
        ),
        migrations.RemoveField(
            model_name="section",
            name="capacity",
        ),
        migrations.AddConstraint(
            model_name="student",
            constraint=models.UniqueConstraint(
                condition=models.Q(("section__isnull", False)),
                fields=("classroom", "section", "roll_number"),
                name="unique_roll_per_class_section",
            ),
        ),
        migrations.AlterModelOptions(
            name="section",
            options={"ordering": ["name"]},
        ),
        migrations.AddConstraint(
            model_name="section",
            constraint=models.UniqueConstraint(
                fields=("name",),
                name="unique_section_name",
            ),
        ),
    ]
