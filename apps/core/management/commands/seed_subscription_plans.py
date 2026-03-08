"""Create default SubscriptionPlan records: Basic (₹5350/year), Pro (₹9999+/year)."""
from django.core.management.base import BaseCommand

from apps.core.models import SubscriptionPlan


class Command(BaseCommand):
    help = "Create default subscription plans: Basic and Pro"

    def handle(self, *args, **options):
        basic, created = SubscriptionPlan.objects.update_or_create(
            name="Basic Plan",
            defaults={
                "price": 5350,
                "billing_cycle": SubscriptionPlan.BillingCycle.YEARLY,
                "description": "Essential features for small schools",
                "features": ["fee_billing", "attendance", "marks", "parent_portal", "staff_attendance", "inventory"],
                "is_active": True,
            },
        )
        self.stdout.write(f"Basic Plan: {'Created' if created else 'Updated'} - Rs {basic.price}/{basic.billing_cycle}")

        pro, created = SubscriptionPlan.objects.update_or_create(
            name="Pro Plan",
            defaults={
                "price": 9999,
                "billing_cycle": SubscriptionPlan.BillingCycle.YEARLY,
                "description": "Full feature set: online admissions, library, hostel, transport, API access",
                "features": [
                    "fee_billing", "attendance", "marks", "parent_portal", "staff_attendance",
                    "inventory", "online_admissions", "online_results", "topper_list", "library",
                    "hostel", "transport", "custom_branding", "api_access", "priority_support",
                ],
                "is_active": True,
            },
        )
        self.stdout.write(self.style.SUCCESS(f"Pro Plan: {'Created' if created else 'Updated'} - Rs {pro.price}+/{pro.billing_cycle}"))
