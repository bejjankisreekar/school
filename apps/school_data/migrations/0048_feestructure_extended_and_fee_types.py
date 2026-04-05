"""Extended FeeStructure fields + standard fee types seed. Idempotent on PostgreSQL."""

from django.db import migrations, models


def seed_standard_fee_types(apps, schema_editor):
    FeeType = apps.get_model("school_data", "FeeType")
    specs = [
        ("Tuition Fee", "TUITION"),
        ("Admission Fee", "ADMISSION"),
        ("Annual Fee", "ANNUAL"),
        ("Exam Fee", "EXAM"),
        ("Books Fee", "BOOKS"),
        ("Uniform Fee", "UNIFORM"),
        ("Transport Fee", "TRANSPORT"),
        ("Hostel Fee", "HOSTEL"),
        ("Lab Fee", "LAB"),
        ("Sports Fee", "SPORTS"),
        ("Activity Fee", "ACTIVITY"),
        ("Library Fee", "LIBRARY"),
        ("Miscellaneous Fee", "MISC"),
    ]
    for name, code in specs:
        if FeeType.objects.filter(code=code).exists():
            continue
        if FeeType.objects.filter(name__iexact=name).exists():
            continue
        FeeType.objects.create(
            name=name,
            code=code,
            description="",
            is_active=True,
        )


def noop_reverse(apps, schema_editor):
    pass


def _feestructure_extended_columns_forward(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as c:
        c.execute(
            """
            ALTER TABLE school_data_feestructure
                ADD COLUMN IF NOT EXISTS line_name varchar(200) NOT NULL DEFAULT '';
            ALTER TABLE school_data_feestructure
                ADD COLUMN IF NOT EXISTS first_due_date date NULL;
            ALTER TABLE school_data_feestructure
                ADD COLUMN IF NOT EXISTS installments_enabled boolean NOT NULL DEFAULT false;
            ALTER TABLE school_data_feestructure
                ADD COLUMN IF NOT EXISTS late_fine_rule varchar(120) NOT NULL DEFAULT '';
            ALTER TABLE school_data_feestructure
                ADD COLUMN IF NOT EXISTS discount_allowed boolean NOT NULL DEFAULT true;
            """
        )


def _feestructure_extended_columns_backward(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0047_feestructure_section_scope"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="feestructure",
                    name="line_name",
                    field=models.CharField(
                        blank=True,
                        default="",
                        help_text="Optional label on receipts (defaults to fee type name if empty).",
                        max_length=200,
                    ),
                ),
                migrations.AddField(
                    model_name="feestructure",
                    name="first_due_date",
                    field=models.DateField(
                        blank=True,
                        help_text="When set, used as the due date for auto-created student fee lines (otherwise computed from due day).",
                        null=True,
                    ),
                ),
                migrations.AddField(
                    model_name="feestructure",
                    name="installments_enabled",
                    field=models.BooleanField(
                        default=False,
                        help_text="Marks this head as installment-capable (schedules are configured separately).",
                    ),
                ),
                migrations.AddField(
                    model_name="feestructure",
                    name="late_fine_rule",
                    field=models.CharField(
                        blank=True,
                        default="",
                        help_text="Reference label for late-fine policy (configure rules under Late Fine Rules).",
                        max_length=120,
                    ),
                ),
                migrations.AddField(
                    model_name="feestructure",
                    name="discount_allowed",
                    field=models.BooleanField(
                        default=True,
                        help_text="If disabled, staff are discouraged from applying concessions on this head.",
                    ),
                ),
                migrations.AlterField(
                    model_name="feestructure",
                    name="frequency",
                    field=models.CharField(
                        choices=[
                            ("ONE_TIME", "One Time"),
                            ("MONTHLY", "Monthly"),
                            ("QUARTERLY", "Quarterly"),
                            ("HALF_YEARLY", "Half Yearly"),
                            ("YEARLY", "Yearly"),
                            ("TERM_WISE", "Term Wise"),
                            ("SEMESTER", "Semester"),
                        ],
                        default="MONTHLY",
                        help_text="Billing cycle for display and planning.",
                        max_length=20,
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(
                    _feestructure_extended_columns_forward,
                    _feestructure_extended_columns_backward,
                ),
                migrations.RunPython(seed_standard_fee_types, noop_reverse),
            ],
        ),
    ]
