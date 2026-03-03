# School Management Upgrade: Section, Student, Teacher
from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


def create_sections_from_classrooms(apps, schema_editor):
    """Create default Section for each ClassRoom (A->Alpha, B->Beta, etc.)."""
    ClassRoom = apps.get_model("core", "ClassRoom")
    Section = apps.get_model("core", "Section")
    mapping = {"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta", "E": "Sigma", "": "Default"}
    for cr in ClassRoom.objects.all():
        letter = cr.section or "A"
        name = mapping.get(letter, letter)
        Section.objects.get_or_create(classroom=cr, school=cr.school, defaults={"name": name})


def migrate_student_sections(apps, schema_editor):
    """Link students to Section by classroom + old section letter."""
    Student = apps.get_model("core", "Student")
    Section = apps.get_model("core", "Section")
    mapping = {"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta", "E": "Sigma", "": "Default"}
    for s in Student.objects.filter(classroom__isnull=False):
        try:
            letter = s._old_section or "A"
        except AttributeError:
            letter = "A"
        name = mapping.get(str(letter).strip() or "A", "Alpha")
        section = Section.objects.filter(classroom=s.classroom, name=name).first()
        if section:
            s.section_new = section
            s.save(update_fields=["section_new"])


def migrate_teacher_subjects(apps, schema_editor):
    """Copy teacher.subject to teacher.subjects M2M."""
    Teacher = apps.get_model("core", "Teacher")
    for t in Teacher.objects.filter(subject__isnull=False):
        t.subjects.add(t.subject)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_exam_and_marks_system"),
    ]

    operations = [
        # 1. Create Section model
        migrations.CreateModel(
            name="Section",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=50)),
                ("classroom", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sections", to="core.classroom")),
                ("school", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sections", to="core.school")),
            ],
            options={"ordering": ["classroom", "name"], "unique_together": {("classroom", "name")}},
        ),
        migrations.RunPython(create_sections_from_classrooms, noop),
        # 2. Alter ClassRoom - section blank
        migrations.AlterField(
            model_name="classroom",
            name="section",
            field=models.CharField(blank=True, max_length=10),
        ),
        # 3. Student: rename section, add section FK
        migrations.RenameField(model_name="student", old_name="section", new_name="_old_section"),
        migrations.AddField(
            model_name="student",
            name="section_new",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="students", to="core.section"),
        ),
        migrations.RunPython(migrate_student_sections, noop),
        migrations.RemoveField(model_name="student", name="_old_section"),
        migrations.RenameField(model_name="student", old_name="section_new", new_name="section"),
        # 4. Student: remove grade, add new fields
        migrations.RemoveField(model_name="student", name="grade"),
        migrations.AddField(
            model_name="student",
            name="admission_number",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name="student",
            name="date_of_birth",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="student",
            name="parent_name",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="student",
            name="parent_phone",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddConstraint(
            model_name="student",
            constraint=models.UniqueConstraint(condition=Q(section__isnull=False), fields=("section", "roll_number"), name="unique_roll_per_section"),
        ),
        # 5. Teacher: add M2M and new fields
        migrations.AddField(
            model_name="teacher",
            name="employee_id",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="teacher",
            name="phone_number",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="teacher",
            name="subjects",
            field=models.ManyToManyField(blank=True, related_name="teachers", to="core.subject"),
        ),
        migrations.AddField(
            model_name="teacher",
            name="classrooms",
            field=models.ManyToManyField(blank=True, related_name="assigned_teachers", to="core.classroom"),
        ),
        migrations.AlterField(
            model_name="teacher",
            name="subject",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="teachers_legacy", to="core.subject"),
        ),
        migrations.RunPython(migrate_teacher_subjects, noop),
    ]
