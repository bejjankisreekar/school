"""Create default Basic and Pro plans, assign Basic to schools without a plan."""
from django.core.management.base import BaseCommand

from apps.core.models import Plan
from apps.customers.models import School


class Command(BaseCommand):
    help = "Create default plans (Basic, Pro) and assign Basic to schools without a plan"

    def handle(self, *args, **options):
        basic, _ = Plan.objects.get_or_create(
            plan_type=Plan.PlanType.BASIC,
            defaults={"name": "Basic Plan"},
        )
        self.stdout.write(f"Plan: {basic}")

        pro, _ = Plan.objects.get_or_create(
            plan_type=Plan.PlanType.PRO,
            defaults={"name": "Pro Plan"},
        )
        self.stdout.write(f"Plan: {pro}")

        updated = School.objects.filter(subscription_plan__isnull=True).update(subscription_plan=basic)
        self.stdout.write(self.style.SUCCESS(f"Assigned Basic plan to {updated} schools"))
