"""
Seed SaaS Plan and Feature models: Starter, Growth, Enterprise.
Run: python manage.py seed_saas_plans
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.customers.models import Plan, Feature


FEATURES = [
    ("Student Management", "students", "Student records, classes, sections"),
    ("Teacher Management", "teachers", "Teacher profiles and assignments"),
    ("Attendance", "attendance", "Student and staff attendance"),
    ("Exam Management", "exams", "Exams, marks, report cards"),
    ("Fees Management", "fees", "Fee structure and collection"),
    ("Payroll", "payroll", "Salary components and payslips"),
    ("Library", "library", "Book management and issues"),
    ("Transport", "transport", "Routes and vehicle management"),
    ("Hostel", "hostel", "Hostel and room allocation"),
    ("Reports", "reports", "Reports and analytics"),
    ("Timetable", "timetable", "Class and teacher timetables"),
    ("Homework", "homework", "Homework assignments"),
    ("Inventory", "inventory", "Inventory and invoicing"),
    ("AI Reports", "ai_reports", "AI-powered reports"),
    ("Online Admission", "online_admission", "Public admission forms"),
    ("Topper List", "topper_list", "Exam toppers"),
    ("Custom Branding", "custom_branding", "Logo and theme customization"),
    ("SMS Notifications", "sms", "Send SMS notifications to parents and students"),
    ("Online Results", "online_results", "View exam results online"),
    ("API Access", "api_access", "Allow public/internal API access"),
    ("AI Marksheet Summaries", "ai_marksheet_summaries", "AI summaries for marksheets"),
    ("Priority Support", "priority_support", "Faster support response"),
]

STARTER_CODES = ["students", "teachers", "attendance", "exams", "timetable", "homework", "reports"]
GROWTH_CODES = STARTER_CODES + ["fees", "inventory", "ai_reports", "sms"]
ENTERPRISE_CODES = GROWTH_CODES + ["payroll", "library", "transport", "hostel", "online_admission", "topper_list", "custom_branding"]
ENTERPRISE_CODES = ENTERPRISE_CODES + ["online_results", "api_access", "ai_marksheet_summaries", "priority_support"]


class Command(BaseCommand):
    help = "Create SaaS plans (Starter, Growth, Enterprise) and features"

    def handle(self, *args, **options):
        # Create features
        feature_objs = {}
        for name, code, desc in FEATURES:
            obj, created = Feature.objects.update_or_create(
                code=code,
                defaults={"name": name, "description": desc},
            )
            feature_objs[code] = obj
            self.stdout.write(f"  Feature: {obj}")

        # Starter
        starter, _ = Plan.objects.update_or_create(
            name="Starter",
            defaults={
                "price_per_student": Decimal("29"),
                "description": "Essential modules for small schools",
            },
        )
        # Non-destructive seeding: only add missing features.
        starter.features.add(*[feature_objs[c] for c in STARTER_CODES if c in feature_objs])
        self.stdout.write(self.style.SUCCESS(f"Plan: {starter}"))

        # Growth
        growth, _ = Plan.objects.update_or_create(
            name="Growth",
            defaults={
                "price_per_student": Decimal("49"),
                "description": "Fee management and reporting",
            },
        )
        # Non-destructive seeding: only add missing features.
        growth.features.add(*[feature_objs[c] for c in GROWTH_CODES if c in feature_objs])
        self.stdout.write(self.style.SUCCESS(f"Plan: {growth}"))

        # Enterprise
        enterprise, _ = Plan.objects.update_or_create(
            name="Enterprise",
            defaults={
                "price_per_student": Decimal("79"),
                "description": "All modules enabled",
            },
        )
        # Non-destructive seeding: only add missing features.
        enterprise.features.add(*[feature_objs[c] for c in ENTERPRISE_CODES if c in feature_objs])
        self.stdout.write(self.style.SUCCESS(f"Plan: {enterprise}"))

        self.stdout.write(self.style.SUCCESS("SaaS plans and features seeded successfully."))
