import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("customers", "0020_school_saas_billing_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="billing_student_count_override",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="If set, Control Center billing uses this headcount instead of live tenant count.",
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="SchoolBillingAuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[
                    ("billing_terms", "Billing terms"),
                    ("student_override", "Student count override"),
                    ("plan_change", "Plan change"),
                    ("status", "Account status"),
                    ("invoice", "Invoice"),
                    ("payment", "Payment"),
                ], db_index=True, max_length=32)),
                ("summary", models.CharField(max_length=512)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="school_billing_audit_logs",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("school", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="billing_audit_logs",
                    to="customers.school",
                )),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="SchoolGeneratedInvoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("invoice_number", models.CharField(db_index=True, max_length=64, unique=True)),
                ("status", models.CharField(
                    choices=[("issued", "Issued"), ("paid", "Paid"), ("void", "Void")],
                    db_index=True,
                    default="issued",
                    max_length=16,
                )),
                ("include_gst", models.BooleanField(default=False)),
                ("gst_rate_percent", models.DecimalField(decimal_places=2, default=18, max_digits=5)),
                ("student_count", models.PositiveIntegerField(default=0, help_text="Headcount used on invoice.")),
                ("tenant_student_count", models.PositiveIntegerField(default=0, help_text="Live tenant student count at generation time.")),
                ("plan_label", models.CharField(blank=True, max_length=120)),
                ("plan_price_per_student", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("base_amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("extra_amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("concession_amount", models.DecimalField(decimal_places=2, default=0, help_text="Discount magnitude (positive rupees).", max_digits=12)),
                ("subtotal_before_gst", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("gst_amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("grand_total", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("snapshot", models.JSONField(blank=True, default=dict)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("paid_notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_school_generated_invoices",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("school", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="generated_invoices",
                    to="customers.school",
                )),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
