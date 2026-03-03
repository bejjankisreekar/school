# Timetable: teacher FK -> teachers M2M
from django.db import migrations, models


def migrate_teacher_to_teachers(apps, schema_editor):
    """Copy timetable.teacher to timetable.teachers M2M."""
    Timetable = apps.get_model("timetable", "Timetable")
    for t in Timetable.objects.filter(teacher__isnull=False):
        t.teachers_new.add(t.teacher)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_school_management_upgrade"),
        ("timetable", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="timetable",
            name="teachers_new",
            field=models.ManyToManyField(blank=True, related_name="timetable_entries", to="core.teacher"),
        ),
        migrations.RunPython(migrate_teacher_to_teachers, noop),
        migrations.RemoveField(
            model_name="timetable",
            name="teacher",
        ),
        migrations.RenameField(
            model_name="timetable",
            old_name="teachers_new",
            new_name="teachers",
        ),
    ]
