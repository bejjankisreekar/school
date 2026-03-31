"""
Seed internal billing rows: trial, basic, pro (map to Starter / Enterprise for modules).
- trial: 14-day trial (Starter modules)
- basic: paid Starter (₹39/student/month)
- pro: paid Enterprise (₹59/student/month)
Run: python manage.py seed_subscription_plans
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.customers.models import SubscriptionPlan


class Command(BaseCommand):
    help = "Create Trial, Basic, Pro subscription plans"

    def handle(self, *args, **options):
        plans = [
            {"name": "trial", "price_per_student": Decimal("0"), "duration_days": 14},
            {"name": "basic", "price_per_student": Decimal("39"), "duration_days": 365},
            {"name": "pro", "price_per_student": Decimal("59"), "duration_days": 365},
        ]
        for p in plans:
            obj, created = SubscriptionPlan.objects.update_or_create(
                name=p["name"],
                defaults={
                    "price_per_student": p["price_per_student"],
                    "duration_days": p["duration_days"],
                    "is_active": True,
                },
            )
            self.stdout.write(
                self.style.SUCCESS(f"{'Created' if created else 'Updated'}: {obj}")
            )
        self.stdout.write(self.style.SUCCESS("Subscription plans ready."))
