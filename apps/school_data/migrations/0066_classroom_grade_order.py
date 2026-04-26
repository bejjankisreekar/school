# Generated manually for grade/class sort order

import re

from django.db import migrations, models


def _fill_grade_order(apps, schema_editor):
    ClassRoom = apps.get_model("school_data", "ClassRoom")
    rx = re.compile(r"\d+")
    for cr in ClassRoom.objects.all().only("id", "name", "grade_order"):
        m = rx.search((cr.name or "").strip())
        n = int(m.group(0)) if m else 0
        if cr.grade_order != n:
            ClassRoom.objects.filter(pk=cr.pk).update(grade_order=n)


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0065_feestructure_batch_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="classroom",
            name="grade_order",
            field=models.PositiveIntegerField(
                db_index=True,
                default=0,
                help_text="Sort position: lower = earlier grade (1 before 10). Filled from name if left 0.",
            ),
        ),
        migrations.RunPython(_fill_grade_order, migrations.RunPython.noop),
    ]
