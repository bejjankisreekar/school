"""Seed sample fixed-amount coupons (₹5 / ₹10). Run: python manage.py seed_billing_coupons"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.customers.models import Coupon


class Command(BaseCommand):
    help = "Create or update SAVE5 and SAVE10 fixed coupons (unlimited uses by default)"

    def handle(self, *args, **options):
        with transaction.atomic():
            c5, cr5 = Coupon.objects.update_or_create(
                code="SAVE5",
                defaults={
                    "discount_type": Coupon.DiscountType.FIXED,
                    "discount_value": Decimal("5"),
                    "max_usage": 0,
                    "is_active": True,
                },
            )
            c10, cr10 = Coupon.objects.update_or_create(
                code="SAVE10",
                defaults={
                    "discount_type": Coupon.DiscountType.FIXED,
                    "discount_value": Decimal("10"),
                    "max_usage": 0,
                    "is_active": True,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"{'Created' if cr5 else 'Updated'} {c5}"))
        self.stdout.write(self.style.SUCCESS(f"{'Created' if cr10 else 'Updated'} {c10}"))
