# Custom migration: convert User.school_id from bigint to varchar(code)

import django.db.models.deletion
from django.db import migrations, models

CONSTRAINT = "accounts_user_school_id_815fb93b_fk_core_school_id"


def convert_user_school(apps, schema_editor):
    with schema_editor.connection.cursor() as c:
        c.execute(f'ALTER TABLE "accounts_user" DROP CONSTRAINT IF EXISTS "{CONSTRAINT}"')
        c.execute('ALTER TABLE "accounts_user" ADD COLUMN school_id_new VARCHAR(50)')
        c.execute(
            'UPDATE "accounts_user" SET school_id_new = '
            '(SELECT code FROM core_school WHERE core_school.id = accounts_user.school_id)'
        )
        c.execute('ALTER TABLE "accounts_user" DROP COLUMN school_id')
        c.execute('ALTER TABLE "accounts_user" RENAME COLUMN school_id_new TO school_id')
        c.execute(
            'ALTER TABLE "accounts_user" ADD CONSTRAINT "accounts_user_school_id_fk_code" '
            "FOREIGN KEY (school_id) REFERENCES core_school(code) ON DELETE SET NULL"
        )


def reverse_convert(apps, schema_editor):
    with schema_editor.connection.cursor() as c:
        c.execute('ALTER TABLE "accounts_user" DROP CONSTRAINT IF EXISTS "accounts_user_school_id_fk_code"')
        c.execute('ALTER TABLE "accounts_user" ADD COLUMN school_id_old BIGINT')
        c.execute(
            'UPDATE "accounts_user" SET school_id_old = '
            '(SELECT id FROM core_school WHERE core_school.code = accounts_user.school_id)'
        )
        c.execute('ALTER TABLE "accounts_user" DROP COLUMN school_id')
        c.execute('ALTER TABLE "accounts_user" RENAME COLUMN school_id_old TO school_id')
        c.execute(
            f'ALTER TABLE "accounts_user" ADD CONSTRAINT "{CONSTRAINT}" '
            "FOREIGN KEY (school_id) REFERENCES core_school(id) ON DELETE SET NULL"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_user_school_alter_user_role"),
        ("core", "0009_school_fk_to_code"),
    ]

    operations = [
        migrations.RunPython(convert_user_school, reverse_convert),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="user",
                    name="school",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="users",
                        to="core.school",
                        to_field="code",
                        db_index=True,
                    ),
                ),
            ],
        ),
    ]
