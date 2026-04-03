"""
Seed platform plans: Starter (₹39), Standard (₹59), Enterprise (₹79) per student / month.
Removes legacy extra Plan rows after migrating schools.
Run: python manage.py seed_saas_plans
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.customers.models import Plan, Feature, School


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
    ("Schedules", "timetable", "Class and teacher schedules"),
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

STARTER_CODES = [
    "students",
    "teachers",
    "attendance",
    "exams",
    "timetable",
    "homework",
    "reports",
]
STANDARD_CODES = STARTER_CODES + [
    "fees",
    "inventory",
    "ai_reports",
    "sms",
    "payroll",
    "library",
    "transport",
    "hostel",
    "online_admission",
    "topper_list",
    "online_results",
]
ENTERPRISE_CODES = STANDARD_CODES + [
    "custom_branding",
    "api_access",
    "ai_marksheet_summaries",
    "priority_support",
]

LEGACY_TO_STARTER = frozenset({"Core"})
LEGACY_TO_STANDARD = frozenset({"Growth"})
LEGACY_TO_ENTERPRISE = frozenset({"Advance"})

KEEP_PLAN_NAMES = frozenset({"Starter", "Standard", "Enterprise"})


class Command(BaseCommand):
    help = "Ensure Starter, Standard, and Enterprise SaaS plans exist with correct prices"

    def handle(self, *args, **options):
        with transaction.atomic():
            feature_objs = {}
            for name, code, desc in FEATURES:
                obj, _ = Feature.objects.update_or_create(
                    code=code,
                    defaults={"name": name, "description": desc},
                )
                feature_objs[code] = obj
                self.stdout.write(f"  Feature: {obj}")

            starter, _ = Plan.objects.update_or_create(
                name="Starter",
                defaults={
                    "price_per_student": Decimal("39"),
                    "billing_cycle": Plan.BillingCycle.MONTHLY,
                    "is_active": True,
                    "description": "Essential modules — attendance, exams, timetable, homework, and reports.",
                },
            )
            starter.features.set([feature_objs[c] for c in STARTER_CODES if c in feature_objs])
            self.stdout.write(self.style.SUCCESS(f"Plan: {starter}"))

            standard, _ = Plan.objects.update_or_create(
                name="Standard",
                defaults={
                    "price_per_student": Decimal("59"),
                    "billing_cycle": Plan.BillingCycle.MONTHLY,
                    "is_active": True,
                    "description": "Mid tier — fees, payroll, library, transport, admissions, and more.",
                },
            )
            standard.features.set([feature_objs[c] for c in STANDARD_CODES if c in feature_objs])
            self.stdout.write(self.style.SUCCESS(f"Plan: {standard}"))

            enterprise, _ = Plan.objects.update_or_create(
                name="Enterprise",
                defaults={
                    "price_per_student": Decimal("79"),
                    "billing_cycle": Plan.BillingCycle.MONTHLY,
                    "is_active": True,
                    "description": "Full platform — all Standard modules plus API, custom branding, and priority support.",
                },
            )
            enterprise.features.set([feature_objs[c] for c in ENTERPRISE_CODES if c in feature_objs])
            self.stdout.write(self.style.SUCCESS(f"Plan: {enterprise}"))

            for old_name in LEGACY_TO_STARTER:
                n = School.objects.filter(saas_plan__name=old_name).update(saas_plan=starter)
                if n:
                    self.stdout.write(f"  Migrated {n} school(s) from {old_name} -> Starter")

            for old_name in LEGACY_TO_STANDARD:
                n = School.objects.filter(saas_plan__name=old_name).update(saas_plan=standard)
                if n:
                    self.stdout.write(f"  Migrated {n} school(s) from {old_name} -> Standard")

            for old_name in LEGACY_TO_ENTERPRISE:
                n = School.objects.filter(saas_plan__name=old_name).update(saas_plan=enterprise)
                if n:
                    self.stdout.write(f"  Migrated {n} school(s) from {old_name} -> Enterprise")

            for school in School.objects.filter(saas_plan__isnull=True).select_related("plan"):
                sp = school.plan
                if not sp:
                    continue
                nm = (sp.name or "").lower()
                if nm == "pro":
                    school.saas_plan = enterprise
                elif nm == "trial":
                    school.saas_plan = starter
                elif nm == "basic":
                    school.saas_plan = starter
                else:
                    continue
                school.save(update_fields=["saas_plan"])
                self.stdout.write(f"  Linked {school.code} saas_plan from subscription ({nm})")

            deleted, _ = Plan.objects.exclude(name__in=KEEP_PLAN_NAMES).delete()
            if deleted:
                self.stdout.write(self.style.WARNING(f"  Removed {deleted} extra plan row(s)."))

        self.stdout.write(self.style.SUCCESS("Starter, Standard, and Enterprise plans are active."))
