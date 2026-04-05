from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0014_school_public_profile_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="payslip_format",
            field=models.CharField(
                choices=[
                    ("corporate", "Corporate — modern cards (recommended)"),
                    ("classic", "Classic — single-sheet tables"),
                    ("minimal", "Minimal — compact one-page"),
                ],
                default="corporate",
                help_text="Layout for employee payslips (on-screen view and PDF).",
                max_length=20,
            ),
        ),
    ]
