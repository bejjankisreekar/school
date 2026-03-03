# Custom migration: convert school_id from bigint (FK to id) to varchar (FK to code)

import django.db.models.deletion
from django.db import migrations, models


# Constraint names from existing DB (run check_constraints.py if these change)
CONSTRAINTS = {
    "core_academicyear": "core_academicyear_school_id_31970e6c_fk_core_school_id",
    "core_classroom": "core_classroom_school_id_4254d658_fk_core_school_id",
    "core_exam": "core_exam_school_id_f2980dd8_fk_core_school_id",
    "core_section": "core_section_school_id_062e9a52_fk_core_school_id",
    "core_subject": "core_subject_school_id_956b9bc5_fk_core_school_id",
}

TABLES = [
    "core_academicyear",
    "core_classroom",
    "core_section",
    "core_exam",
    "core_subject",
]


def convert_school_fks(apps, schema_editor):
    """Convert school_id from bigint to varchar(code) for core tables."""
    with schema_editor.connection.cursor() as c:
        for table in TABLES:
            cname = CONSTRAINTS.get(table)
            if not cname:
                continue
            # 1. Drop FK constraint
            c.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{cname}"')
            # 2. Add temp column, populate, swap
            c.execute(f'ALTER TABLE "{table}" ADD COLUMN school_id_new VARCHAR(50)')
            c.execute(
                f'UPDATE "{table}" SET school_id_new = '
                f'(SELECT code FROM core_school WHERE core_school.id = "{table}".school_id)'
            )
            c.execute(f'ALTER TABLE "{table}" DROP COLUMN school_id')
            c.execute(f'ALTER TABLE "{table}" RENAME COLUMN school_id_new TO school_id')
            # 3. Add new FK to core_school(code)
            c.execute(
                f'ALTER TABLE "{table}" ADD CONSTRAINT "{table}_school_id_fk_code" '
                "FOREIGN KEY (school_id) REFERENCES core_school(code) ON DELETE CASCADE"
            )


def reverse_convert(apps, schema_editor):
    """Reverse: convert school_id back to bigint (FK to id)."""
    with schema_editor.connection.cursor() as c:
        for table in reversed(TABLES):
            c.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{table}_school_id_fk_code"')
            c.execute(f'ALTER TABLE "{table}" ADD COLUMN school_id_old BIGINT')
            c.execute(
                f'UPDATE "{table}" SET school_id_old = '
                f'(SELECT id FROM core_school WHERE core_school.code = "{table}".school_id)'
            )
            c.execute(f'ALTER TABLE "{table}" DROP COLUMN school_id')
            c.execute(f'ALTER TABLE "{table}" RENAME COLUMN school_id_old TO school_id')
            c.execute(
                f'ALTER TABLE "{table}" ADD CONSTRAINT "{CONSTRAINTS[table]}" '
                "FOREIGN KEY (school_id) REFERENCES core_school(id) ON DELETE CASCADE"
            )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_alter_academicyear_options"),
    ]

    operations = [
        migrations.RunPython(convert_school_fks, reverse_convert),
        migrations.SeparateDatabaseAndState(
            state_operations=[
        migrations.AlterField(
            model_name="academicyear",
            name="school",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="academic_years",
                to="core.school",
                to_field="code",
            ),
        ),
        migrations.AlterField(
            model_name="classroom",
            name="school",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="classrooms",
                to="core.school",
                to_field="code",
            ),
        ),
        migrations.AlterField(
            model_name="exam",
            name="school",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="exams",
                to="core.school",
                to_field="code",
            ),
        ),
        migrations.AlterField(
            model_name="section",
            name="school",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="sections",
                to="core.school",
                to_field="code",
            ),
        ),
        migrations.AlterField(
            model_name="subject",
            name="school",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="subjects",
                to="core.school",
                to_field="code",
            ),
        ),
            ],
        ),
    ]
