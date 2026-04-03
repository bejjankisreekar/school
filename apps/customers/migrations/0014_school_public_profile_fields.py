from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0013_standard_plan_enterprise_price"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="date_of_establishment",
            field=models.DateField(
                blank=True,
                help_text="Official date the institution was established or recognized.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="school",
            name="website",
            field=models.URLField(blank=True, help_text="Official website URL.", max_length=500),
        ),
        migrations.AddField(
            model_name="school",
            name="registration_number",
            field=models.CharField(
                blank=True,
                help_text="Government / board registration or UDISE / affiliation number.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="school",
            name="board_affiliation",
            field=models.CharField(
                blank=True,
                help_text="e.g. CBSE, ICSE, State Board, IB.",
                max_length=120,
            ),
        ),
    ]
