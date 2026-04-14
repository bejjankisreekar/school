"""
Seed default role-based sidebar menu items (public schema).

Run:
    python manage.py seed_sidebar_menu
    python manage.py seed_sidebar_menu --reset
"""

from django.core.management.base import BaseCommand

from apps.core.models import SidebarMenuItem


def _mk(role: str, label: str, route_name: str, icon: str, order: int, *, feature_code: str = "", parent=None):
    return {
        "role": role,
        "label": label,
        "route_name": route_name,
        "icon": icon,
        "display_order": order,
        "feature_code": feature_code,
        "parent": parent,
        "is_visible": True,
        "is_active": True,
    }


class Command(BaseCommand):
    help = "Seed default sidebar menu items for roles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing SidebarMenuItem rows before seeding.",
        )
        parser.add_argument(
            "--ensure",
            action="store_true",
            help="Insert missing default items without overwriting existing rows.",
        )

    def handle(self, *args, **options):
        if options.get("reset"):
            SidebarMenuItem.objects.all().delete()
            self.stdout.write(self.style.WARNING("Deleted existing SidebarMenuItem rows."))

        has_rows = SidebarMenuItem.objects.exists()
        if has_rows and not options.get("ensure"):
            self.stdout.write("SidebarMenuItem rows already exist. Use --reset to recreate defaults, or --ensure to add missing defaults.")
            return

        # SUPERADMIN
        rows = [
            _mk("SUPERADMIN", "Dashboard", "core:super_admin_dashboard", "bi bi-grid-1x2", 1),
            _mk("SUPERADMIN", "Control Center", "core:superadmin_control_center", "bi bi-sliders2", 2),
            _mk("SUPERADMIN", "Schools", "core:superadmin_schools_overview", "bi bi-buildings", 3),
            _mk("SUPERADMIN", "Financials", "core:superadmin_financials", "bi bi-currency-exchange", 4),
            _mk("SUPERADMIN", "Global Teachers", "core:superadmin_global_teachers", "bi bi-person-badge", 5),
            _mk("SUPERADMIN", "Global Students", "core:superadmin_global_students", "bi bi-people", 6),
        ]

        # ADMIN (School Admin)
        admin = "ADMIN"
        rows += [
            _mk(admin, "Dashboard", "core:admin_dashboard", "bi bi-grid-1x2", 1),
            _mk(admin, "Admissions", "core:school_admissions_list", "bi bi-person-plus", 2),
            _mk(admin, "Students", "core:school_students_list", "bi bi-people", 3),
            _mk(admin, "Teachers", "core:school_teachers_list", "bi bi-person-badge", 4),
            _mk(admin, "Classes", "core:school_classes", "bi bi-journal-bookmark", 5),
            _mk(admin, "Sections", "core:school_sections", "bi bi-diagram-3", 6),
            _mk(admin, "Subjects", "core:school_subjects", "bi bi-book", 7),
            _mk(admin, "Academic Years", "core:school_academic_years", "bi bi-calendar-range", 8),
            _mk(admin, "Student Attendance", "core:attendance_list", "bi bi-calendar-check", 9, feature_code="attendance"),
            _mk(admin, "Faculty Attendance", "core:school_staff_attendance", "bi bi-person-check", 10, feature_code="attendance"),
            _mk(admin, "Schedules", "timetable:school_timetable_index", "bi bi-calendar3", 11, feature_code="timetable"),
            _mk(admin, "Exams", "core:school_exams_list", "bi bi-clipboard-check", 12, feature_code="exams"),
            _mk(admin, "Homework", "core:school_homework_list", "bi bi-journal-text", 13, feature_code="homework"),
            _mk(admin, "Fees & Billing", "core:billing_dashboard", "bi bi-currency-dollar", 14, feature_code="fees"),
            _mk(admin, "Reports", "reports:dashboard", "bi bi-graph-up-arrow", 15, feature_code="reports"),
            _mk(admin, "Notifications", "notifications:school_notifications", "bi bi-bell", 16),
            _mk(admin, "Library", "core:school_library_index", "bi bi-journals", 17, feature_code="library"),
            _mk(admin, "Hostel", "core:school_hostel_index", "bi bi-house-door", 18, feature_code="hostel"),
            _mk(admin, "Transport", "core:school_transport_index", "bi bi-bus-front", 19, feature_code="transport"),
            _mk(admin, "Branding", "core:school_branding", "bi bi-palette2", 20),
            _mk(admin, "Support", "core:school_support_create", "bi bi-life-preserver", 21),
        ]

        # TEACHER
        teacher = "TEACHER"
        rows += [
            _mk(teacher, "Dashboard", "core:teacher_dashboard", "bi bi-grid-1x2", 1),
            _mk(teacher, "My Students", "core:teacher_students_list", "bi bi-people", 2),
            _mk(teacher, "Attendance", "core:bulk_attendance", "bi bi-calendar-check", 3, feature_code="attendance"),
            _mk(teacher, "Timetable", "timetable:teacher_timetable", "bi bi-calendar3", 4, feature_code="timetable"),
            _mk(teacher, "Homework", "core:homework_list", "bi bi-journal-text", 5, feature_code="homework"),
            _mk(teacher, "Exams / Marks", "core:teacher_exams", "bi bi-clipboard-check", 6, feature_code="exams"),
            _mk(teacher, "Performance", "core:teacher_class_analytics", "bi bi-bar-chart", 7, feature_code="reports"),
        ]

        # STUDENT
        student = "STUDENT"
        rows += [
            _mk(student, "Dashboard", "core:student_dashboard", "bi bi-grid-1x2", 1),
            _mk(student, "Timetable", "timetable:student_timetable", "bi bi-calendar3", 2, feature_code="timetable"),
            _mk(student, "Attendance", "core:student_attendance", "bi bi-calendar-check", 3, feature_code="attendance"),
            _mk(student, "Homework", "core:homework_list", "bi bi-journal-text", 4, feature_code="homework"),
            _mk(student, "Exams & Results", "core:student_exams_list", "bi bi-clipboard-check", 5, feature_code="exams"),
            _mk(student, "Fees", "core:student_fees", "bi bi-currency-dollar", 6, feature_code="fees"),
            _mk(student, "Notifications", "notifications:student_notifications", "bi bi-bell", 7),
            _mk(student, "Reports", "core:student_reports", "bi bi-graph-up-arrow", 8, feature_code="reports"),
            _mk(student, "Profile", "core:student_profile", "bi bi-person-circle", 9),
        ]

        # PARENT (future-ready minimal)
        parent = "PARENT"
        rows += [
            _mk(parent, "Dashboard", "core:parent_dashboard", "bi bi-grid-1x2", 1),
            _mk(parent, "Announcements", "core:parent_announcements", "bi bi-megaphone", 2),
        ]

        if options.get("ensure") and has_rows:
            created = 0
            for r in rows:
                obj, was_created = SidebarMenuItem.objects.get_or_create(
                    role=r["role"],
                    route_name=r["route_name"],
                    defaults=r,
                )
                if was_created:
                    created += 1
            self.stdout.write(self.style.SUCCESS(f"Ensured defaults. Added {created} missing item(s)."))
            return

        SidebarMenuItem.objects.bulk_create([SidebarMenuItem(**r) for r in rows])
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(rows)} sidebar menu items."))

