# Custom migration: convert school_id from bigint to varchar(code) for timetable tables

import django.db.models.deletion
from django.db import migrations, models

CONSTRAINTS = {
    "timetable_timeslot": "timetable_timeslot_school_id_8b952a7e_fk_core_school_id",
    "timetable_timetable": "timetable_timetable_school_id_8b560ed2_fk_core_school_id",
}
TABLES = ["timetable_timeslot", "timetable_timetable"]


def convert_timetable_school_fks(apps, schema_editor):
    with schema_editor.connection.cursor() as c:
        for table in TABLES:
            cname = CONSTRAINTS[table]
            c.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{cname}"')
            c.execute(f'ALTER TABLE "{table}" ADD COLUMN school_id_new VARCHAR(50)')
            c.execute(
                f'UPDATE "{table}" SET school_id_new = '
                f'(SELECT code FROM core_school WHERE core_school.id = "{table}".school_id)'
            )
            c.execute(f'ALTER TABLE "{table}" DROP COLUMN school_id')
            c.execute(f'ALTER TABLE "{table}" RENAME COLUMN school_id_new TO school_id')
            c.execute(
                f'ALTER TABLE "{table}" ADD CONSTRAINT "{table}_school_id_fk_code" '
                "FOREIGN KEY (school_id) REFERENCES core_school(code) ON DELETE CASCADE"
            )


def reverse_convert(apps, schema_editor):
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
        ("core", "0009_school_fk_to_code"),
        ("timetable", "0002_timetable_multi_teacher"),
    ]

    operations = [
        migrations.RunPython(convert_timetable_school_fks, reverse_convert),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="timeslot",
                    name="school",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="time_slots",
                        to="core.school",
                        to_field="code",
                    ),
                ),
                migrations.AlterField(
                    model_name="timetable",
                    name="school",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="timetable_entries",
                        to="core.school",
                        to_field="code",
                    ),
                ),
            ],
        ),
    ]
