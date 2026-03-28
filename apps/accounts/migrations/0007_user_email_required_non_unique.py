from django.db import migrations, models


DROP_EMAIL_UNIQUE_SQL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE table_schema = current_schema()
          AND table_name = 'accounts_user'
          AND constraint_name = 'accounts_user_email_key'
          AND constraint_type = 'UNIQUE'
    ) THEN
        ALTER TABLE accounts_user DROP CONSTRAINT accounts_user_email_key;
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_add_is_first_login"),
    ]

    operations = [
        migrations.RunSQL(sql=DROP_EMAIL_UNIQUE_SQL, reverse_sql=migrations.RunSQL.noop),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(
                blank=False,
                help_text="Required. Can be shared (e.g. parent email for multiple students).",
                max_length=254,
                verbose_name="email address",
            ),
        ),
    ]

