from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_sidebar_menu_item"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnalyticsField",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_on", models.DateTimeField(auto_now_add=True, db_index=True, editable=False)),
                ("modified_on", models.DateTimeField(auto_now=True, editable=False)),
                ("field_key", models.CharField(db_index=True, max_length=80)),
                ("display_label", models.CharField(db_index=True, max_length=160)),
                ("category", models.CharField(blank=True, db_index=True, default="", max_length=60)),
                ("display_order", models.PositiveIntegerField(db_index=True, default=0)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_by", models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="analyticsfield_created", to="accounts.user")),
                ("modified_by", models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="analyticsfield_modified", to="accounts.user")),
            ],
            options={
                "ordering": ["category", "field_key", "display_order", "display_label"],
            },
        ),
        migrations.AddConstraint(
            model_name="analyticsfield",
            constraint=models.UniqueConstraint(fields=("field_key", "display_label"), name="uniq_analyticsfield_key_label"),
        ),
    ]

