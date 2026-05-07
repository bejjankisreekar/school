"""
Seed dynamic SaaS Features + Plans (Basic / Pro / Premium).

This powers plan-based module access via:
School.saas_plan -> Plan.features -> Feature.code
"""

from django.core.management.base import BaseCommand

from apps.customers.models import Plan, School


class Command(BaseCommand):
    help = "Seed Feature + Plan rows for Basic/Pro/Premium (customers.Plan)."

    def add_arguments(self, parser):
        parser.add_argument("--assign-basic", action="store_true", help="Assign Basic to schools with no saas_plan")

    def handle(self, *args, **options):
        from apps.core.plan_features import seed_customer_tier_plans

        self.stdout.write("Seeding customers tier catalog (Basic / Pro / Premium)…")
        seed_customer_tier_plans()
        self.stdout.write(self.style.SUCCESS("Customers plans synced."))

        if options.get("--assign-basic") or options.get("assign_basic"):
            basic = Plan.objects.filter(name="Basic", is_active=True).first()
            if basic:
                updated = School.objects.exclude(schema_name="public").filter(saas_plan__isnull=True).update(saas_plan=basic)
                self.stdout.write(self.style.SUCCESS(f"Assigned Basic to {updated} schools without saas_plan"))

