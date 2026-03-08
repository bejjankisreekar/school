"""Seed default grade definitions for report cards."""
from django.core.management.base import BaseCommand

from apps.school_data.models import Grade


DEFAULT_GRADES = [
    ("A+", 95, 100, "Outstanding"),
    ("A", 90, 94, "Excellent"),
    ("B+", 85, 89, "Very Good"),
    ("B", 80, 84, "Good"),
    ("C+", 75, 79, "Above Average"),
    ("C", 70, 74, "Average"),
    ("D", 60, 69, "Pass"),
    ("F", 0, 59, "Fail"),
]


class Command(BaseCommand):
    help = "Create default grade definitions (A+, A, B+, etc.)"

    def handle(self, *args, **options):
        for name, min_pct, max_pct, desc in DEFAULT_GRADES:
            g, _ = Grade.objects.update_or_create(
                name=name,
                defaults={"min_percentage": min_pct, "max_percentage": max_pct, "description": desc},
            )
            self.stdout.write(f"Grade {g.name}: {g.min_percentage}-{g.max_percentage}%")
        self.stdout.write(self.style.SUCCESS("Grades seeded."))
