# Subject becomes global master; class/teacher/year removed. Merge duplicates + re-point FKs.

from collections import defaultdict

from django.db import migrations, models


def _table_exists(schema_editor, table_name):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema() AND table_name = %s
            """,
            [table_name],
        )
        return cursor.fetchone() is not None


def _merge_duplicate_subjects(apps, schema_editor):
    Subject = apps.get_model("school_data", "Subject")
    try:
        ClassSectionSubjectTeacher = apps.get_model("school_data", "ClassSectionSubjectTeacher")
    except LookupError:
        ClassSectionSubjectTeacher = None
    Marks = apps.get_model("school_data", "Marks")
    Homework = apps.get_model("school_data", "Homework")
    Teacher = apps.get_model("school_data", "Teacher")
    Through = Teacher._meta.get_field("subjects").remote_field.through

    try:
        Timetable = apps.get_model("timetable", "Timetable")
    except LookupError:
        Timetable = None

    subjects = list(Subject.objects.all().order_by("id"))
    groups = defaultdict(list)
    for s in subjects:
        key = (s.name.strip().lower(), (s.code or "").strip().lower())
        groups[key].append(s.id)

    has_csst = bool(ClassSectionSubjectTeacher) and _table_exists(schema_editor, "school_data_classsectionsubjectteacher")

    for _key, ids in groups.items():
        if len(ids) <= 1:
            continue
        canonical = min(ids)
        others = [i for i in ids if i != canonical]

        for oid in others:
            # ClassSectionSubjectTeacher (table added in 0018 if missing historically)
            if not has_csst or not ClassSectionSubjectTeacher:
                pass
            else:
                for m in list(ClassSectionSubjectTeacher.objects.filter(subject_id=oid)):
                    dup = ClassSectionSubjectTeacher.objects.filter(
                        class_obj_id=m.class_obj_id,
                        section_id=m.section_id,
                        subject_id=canonical,
                    ).first()
                    if dup:
                        m.delete()
                    else:
                        m.subject_id = canonical
                        m.save()

            # Marks — avoid unique (student, subject, exam) violations
            for mark in Marks.objects.filter(subject_id=oid):
                conflict = Marks.objects.filter(
                    student_id=mark.student_id,
                    subject_id=canonical,
                    exam_id=mark.exam_id,
                ).exclude(pk=mark.pk).exists()
                if conflict:
                    mark.delete()
                else:
                    mark.subject_id = canonical
                    mark.save()

            Homework.objects.filter(subject_id=oid).update(subject_id=canonical)

            if Timetable:
                Timetable.objects.filter(subject_id=oid).update(subject_id=canonical)

            Teacher.objects.filter(subject_id=oid).update(subject_id=canonical)

            for row in list(Through.objects.filter(subject_id=oid)):
                if Through.objects.filter(teacher_id=row.teacher_id, subject_id=canonical).exists():
                    row.delete()
                else:
                    row.subject_id = canonical
                    row.save()

            Subject.objects.filter(pk=oid).delete()


def _ensure_subject_codes(apps, schema_editor):
    Subject = apps.get_model("school_data", "Subject")
    used = set(
        Subject.objects.exclude(code="").values_list("code", flat=True)
    )
    for s in Subject.objects.all().order_by("id"):
        c = (s.code or "").strip()
        if c:
            continue
        n = 1
        while True:
            cand = f"AUTO{s.id}" if n == 1 else f"AUTO{s.id}-{n}"
            if cand not in used:
                s.code = cand
                s.save(update_fields=["code"])
                used.add(cand)
                break
            n += 1

    # Resolve any duplicate non-blank codes after merge edge cases
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT code FROM school_data_subject
            WHERE code <> ''
            GROUP BY code HAVING COUNT(*) > 1
            """
        )
        dup_codes = [r[0] for r in cur.fetchall()]
    for code in dup_codes:
        qs = Subject.objects.filter(code=code).order_by("id")
        keep = qs.first()
        for s in qs.exclude(pk=keep.pk):
            n = 1
            while True:
                cand = f"{code}-{n}"
                if not Subject.objects.filter(code=cand).exists():
                    s.code = cand
                    s.save(update_fields=["code"])
                    break
                n += 1


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0012_homework_class_section"),
        ("timetable", "0004_alter_timeslot_options_remove_timeslot_school_and_more"),
    ]

    operations = [
        migrations.RunPython(_merge_duplicate_subjects, noop_reverse),
        migrations.RunPython(_ensure_subject_codes, noop_reverse),
        migrations.RemoveConstraint(
            model_name="subject",
            name="unique_subject_code_per_class_year",
        ),
        migrations.RemoveField(
            model_name="subject",
            name="academic_year",
        ),
        migrations.RemoveField(
            model_name="subject",
            name="classroom",
        ),
        migrations.RemoveField(
            model_name="subject",
            name="teacher",
        ),
        migrations.AddConstraint(
            model_name="subject",
            constraint=models.UniqueConstraint(
                condition=models.Q(("code__gt", "")),
                fields=("code",),
                name="unique_subject_code_non_blank",
            ),
        ),
    ]
