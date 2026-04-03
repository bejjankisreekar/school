from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    Plan = apps.get_model("customers", "Plan")
    ent = Plan.objects.filter(name="Enterprise").first()
    if ent:
        ent.price_per_student = Decimal("79.00")
        ent.save(update_fields=["price_per_student"])
    if Plan.objects.filter(name="Standard").exists():
        return
    std = Plan.objects.create(
        name="Standard",
        price_per_student=Decimal("59.00"),
        billing_cycle="monthly",
        is_active=True,
        description="Mid tier — between Basic and Enterprise. Run: python manage.py seed_saas_plans",
    )
    starter = Plan.objects.filter(name="Starter").first()
    if starter:
        fids = list(starter.features.values_list("id", flat=True))
        if fids:
            std.features.set(fids)


def backwards(apps, schema_editor):
    Plan = apps.get_model("customers", "Plan")
    Plan.objects.filter(name="Standard").delete()
    ent = Plan.objects.filter(name="Enterprise").first()
    if ent:
        ent.price_per_student = Decimal("59.00")
        ent.save(update_fields=["price_per_student"])


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0012_remove_platforminvoice_saas_inv_year_month_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
