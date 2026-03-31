from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add marks_teacher_edit_locked using raw SQL with IF NOT EXISTS so it applies cleanly
    on tenant schemas and can recover if a prior migrate attempt left state inconsistent.
    """

    dependencies = [
        ("school_data", "0024_student_gender"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE school_data_exam "
                        "ADD COLUMN IF NOT EXISTS marks_teacher_edit_locked boolean NOT NULL DEFAULT false;"
                    ),
                    reverse_sql=(
                        "ALTER TABLE school_data_exam "
                        "DROP COLUMN IF EXISTS marks_teacher_edit_locked;"
                    ),
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="exam",
                    name="marks_teacher_edit_locked",
                    field=models.BooleanField(
                        default=False,
                        help_text="When true, teachers cannot save mark changes until an admin allows re-editing.",
                    ),
                ),
            ],
        ),
    ]
