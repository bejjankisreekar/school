from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("school_data", "0070_marks_component_marks"),
    ]

    operations = [
        migrations.AddField(
            model_name="homework",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="homework_created",
                to="accounts.user",
            ),
        ),
        migrations.AddField(
            model_name="homework",
            name="created_on",
            field=models.DateTimeField(auto_now_add=True, db_index=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="homework",
            name="modified_by",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="homework_modified",
                to="accounts.user",
            ),
        ),
        migrations.AddField(
            model_name="homework",
            name="modified_on",
            field=models.DateTimeField(auto_now=True, editable=False, null=True),
        ),
    ]

