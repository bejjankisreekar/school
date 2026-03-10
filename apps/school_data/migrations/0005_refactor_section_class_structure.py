# Refactor: Section independent, ClassRoom has M2M to Section
# Step 1: Add new fields and M2M, migrate data, then remove old fields

import django.db.models.deletion
from django.db import migrations, models


def migrate_section_data(apps, schema_editor):
    """Create global sections from existing data and link classes."""
    Section = apps.get_model("school_data", "Section")
    ClassRoom = apps.get_model("school_data", "ClassRoom")
    Student = apps.get_model("school_data", "Student")

    name_to_section = {}  # name -> new Section
    old_to_new = {}  # old_section_id -> new_section_id

    for old_sec in Section.objects.select_related("classroom").all().order_by("id"):
        name = (old_sec.name or "A").strip() or "A"
        if name not in name_to_section:
            new_sec = Section.objects.create(name=name, description="", classroom=None)
            name_to_section[name] = new_sec
        new_sec = name_to_section[name]
        old_to_new[old_sec.id] = new_sec.id
        if old_sec.classroom_id:
            old_sec.classroom.sections.add(new_sec)

    for student in Student.objects.filter(section_id__isnull=False):
        if student.section_id in old_to_new:
            student.section_id = old_to_new[student.section_id]
            student.save(update_fields=["section_id"])

    # Delete old section records (with classroom set) - they are now orphaned
    Section.objects.filter(classroom__isnull=False).delete()


def reverse_migrate(apps, schema_editor):
    """Reverse is lossy - cannot restore old structure from merged sections."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0004_staff_attendance_holiday_other"),
    ]

    operations = [
        # 1. Add Section.description, make classroom nullable for migration
        migrations.AddField(
            model_name="section",
            name="description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AlterField(
            model_name="section",
            name="classroom",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="sections_legacy",
                to="school_data.classroom",
            ),
        ),
        # 2. Add ClassRoom.sections M2M
        migrations.AddField(
            model_name="classroom",
            name="sections",
            field=models.ManyToManyField(
                blank=True,
                related_name="classrooms",
                to="school_data.section",
            ),
        ),
        # 3. Data migration
        migrations.RunPython(migrate_section_data, reverse_migrate),
    ]
