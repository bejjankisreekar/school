"""
FeeStructure optional section + partial unique constraints.

Idempotent on PostgreSQL for tenant schemas (safe if column or indexes already exist).
"""
import django.db.models.deletion
from django.db import migrations, models


def _feestructure_section_forward(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise NotImplementedError("FeeStructure section migration requires PostgreSQL.")
    with schema_editor.connection.cursor() as c:
        c.execute(
            """
            ALTER TABLE school_data_feestructure
                ADD COLUMN IF NOT EXISTS section_id bigint NULL;
            """
        )
        c.execute(
            """
            DO $$
            BEGIN
                ALTER TABLE school_data_feestructure
                    ADD CONSTRAINT school_data_feestructure_section_id_fkey
                    FOREIGN KEY (section_id) REFERENCES school_data_section(id)
                    DEFERRABLE INITIALLY DEFERRED;
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END $$;
            """
        )
        c.execute(
            """
            DO $$
            DECLARE r record;
            BEGIN
                FOR r IN (
                    SELECT c.conname
                    FROM pg_constraint c
                    JOIN pg_class t ON c.conrelid = t.oid
                    WHERE t.relname = 'school_data_feestructure'
                      AND c.contype = 'u'
                      AND pg_get_constraintdef(c.oid) LIKE '%fee_type_id%'
                      AND pg_get_constraintdef(c.oid) LIKE '%classroom_id%'
                      AND pg_get_constraintdef(c.oid) LIKE '%academic_year_id%'
                      AND pg_get_constraintdef(c.oid) NOT LIKE '%section_id%'
                ) LOOP
                    EXECUTE format(
                        'ALTER TABLE school_data_feestructure DROP CONSTRAINT IF EXISTS %I',
                        r.conname
                    );
                END LOOP;
            END $$;
            """
        )
        c.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS school_data_feestructure_unique_class_wide
                ON school_data_feestructure (fee_type_id, classroom_id, academic_year_id)
                WHERE section_id IS NULL;
            """
        )
        c.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS school_data_feestructure_unique_section
                ON school_data_feestructure (fee_type_id, classroom_id, academic_year_id, section_id)
                WHERE section_id IS NOT NULL;
            """
        )


def _feestructure_section_backward(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as c:
        c.execute(
            "DROP INDEX IF EXISTS school_data_feestructure_unique_section;"
        )
        c.execute(
            "DROP INDEX IF EXISTS school_data_feestructure_unique_class_wide;"
        )
        c.execute(
            """
            ALTER TABLE school_data_feestructure
                DROP CONSTRAINT IF EXISTS school_data_feestructure_section_id_fkey;
            """
        )
        c.execute(
            """
            ALTER TABLE school_data_feestructure
                DROP COLUMN IF EXISTS section_id;
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0046_homework_enterprise_columns_if_missing"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="feestructure",
                    name="section",
                    field=models.ForeignKey(
                        blank=True,
                        help_text="Optional: limit this fee head to one section. Leave empty for all sections in the class.",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fee_structures",
                        to="school_data.section",
                    ),
                ),
                migrations.AlterUniqueTogether(
                    name="feestructure",
                    unique_together=set(),
                ),
                migrations.AddConstraint(
                    model_name="feestructure",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(section__isnull=True),
                        fields=("fee_type", "classroom", "academic_year"),
                        name="school_data_feestructure_unique_class_wide",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="feestructure",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(section__isnull=False),
                        fields=("fee_type", "classroom", "academic_year", "section"),
                        name="school_data_feestructure_unique_section",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(_feestructure_section_forward, _feestructure_section_backward),
            ],
        ),
    ]
