# Subject display order for school master list

from django.db import migrations, models


def seed_display_order(apps, schema_editor):
    Subject = apps.get_model("school_data", "Subject")
    rows = list(Subject.objects.all().order_by("name", "id"))
    for i, s in enumerate(rows):
        Subject.objects.filter(pk=s.pk).update(display_order=(i + 1) * 10)


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0063_exam_mark_component"),
    ]

    operations = [
        migrations.AddField(
            model_name="subject",
            name="display_order",
            field=models.PositiveIntegerField(
                db_index=True,
                default=0,
                help_text="Lower numbers appear first in subject pickers and the school subject list.",
            ),
        ),
        migrations.RunPython(seed_display_order, migrations.RunPython.noop),
    ]
