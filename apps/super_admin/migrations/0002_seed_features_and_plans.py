from django.db import migrations


FEATURES = [
    # Academic
    ("Students", "students", "academic"),
    ("Teachers", "teachers", "academic"),
    ("Subjects", "subjects", "academic"),
    ("Classes", "classes", "academic"),
    ("Sections", "sections", "academic"),
    ("Academic Year", "academic_year", "academic"),
    # Operations
    ("Student Attendance", "attendance_student", "operations"),
    ("Teacher Attendance", "attendance_teacher", "operations"),
    ("Timetable", "timetable", "operations"),
    ("Calendar", "calendar", "operations"),
    # Exams
    ("Exams", "exams", "exams"),
    ("Homework", "homework", "exams"),
    # Communication
    ("Messaging", "messaging", "communication"),
    ("Broadcast", "broadcast", "communication"),
    ("Notifications", "notifications", "communication"),
    # Finance
    ("Fees", "fees", "finance"),
    # Analytics / Reports (keep under analytics category using operations-like grouping; stored as 'operations' by choices)
    ("Reports", "reports", "operations"),
    ("Analytics", "analytics", "operations"),
]


def seed(apps, schema_editor):
    Feature = apps.get_model("super_admin", "Feature")
    Plan = apps.get_model("super_admin", "Plan")

    feature_by_code = {}
    for name, code, category in FEATURES:
        obj, _ = Feature.objects.update_or_create(code=code, defaults={"name": name, "category": category})
        feature_by_code[code] = obj

    basic, _ = Plan.objects.update_or_create(name="basic", defaults={"price": 0, "is_active": True})
    pro, _ = Plan.objects.update_or_create(name="pro", defaults={"price": 0, "is_active": True})
    premium, _ = Plan.objects.update_or_create(name="premium", defaults={"price": 0, "is_active": True})

    basic_codes = {
        "students",
        "teachers",
        "subjects",
        "classes",
        "sections",
        "academic_year",
    }
    pro_codes = set(basic_codes) | {
        "attendance_student",
        "attendance_teacher",
        "timetable",
        "exams",
        "homework",
        "messaging",
        "broadcast",
    }
    premium_codes = {c for _n, c, _cat in FEATURES}

    basic.features.set([feature_by_code[c] for c in basic_codes if c in feature_by_code])
    pro.features.set([feature_by_code[c] for c in pro_codes if c in feature_by_code])
    premium.features.set([feature_by_code[c] for c in premium_codes if c in feature_by_code])


def unseed(apps, schema_editor):
    Feature = apps.get_model("super_admin", "Feature")
    Plan = apps.get_model("super_admin", "Plan")
    Plan.objects.filter(name__in=["basic", "pro", "premium"]).delete()
    Feature.objects.filter(code__in=[c for _n, c, _cat in FEATURES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("super_admin", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

