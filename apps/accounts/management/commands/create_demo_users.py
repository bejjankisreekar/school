"""
Django management command to create demo users.
Run: python manage.py create_demo_users

Academic rows (class, section, subject, exam) are created inside each school's
PostgreSQL schema via tenant_context — never in public.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django_tenants.utils import tenant_context

from apps.customers.models import School
from apps.school_data.models import ClassRoom, Exam, Section, Student, Subject, Teacher

User = get_user_model()


class Command(BaseCommand):
    help = "Create demo users: SuperAdmin + multi-school admins/teachers/students"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Recreate users even if they exist (resets password)",
        )

    def handle(self, *args, **options):
        force = options["force"]
        verbosity = options["verbosity"]

        def log(msg, level=1):
            if verbosity >= level:
                self.stdout.write(msg)

        # 1. Super Admin
        if User.objects.filter(username="superadmin").exists() and not force:
            log("SuperAdmin already exists, skipping.")
        else:
            user, created = User.objects.update_or_create(
                username="superadmin",
                defaults={
                    "role": User.Roles.SUPERADMIN,
                    "is_staff": True,
                    "is_superuser": True,
                    "is_active": True,
                },
            )
            user.set_password("admin123")
            user.save()
            log(f"SuperAdmin: {'Created' if created else 'Updated'} (username: superadmin, password: admin123)")

        # 2. Create demo data for two schools, each isolated by `User.school`
        schools_spec = [
            {
                "code": "GVS001",
                "name": "Green Valley School",
                "address": "",
                "prefix": "gvs",
            },
            {
                "code": "BRS001",
                "name": "Blue Ridge School",
                "address": "",
                "prefix": "brs",
            },
        ]

        start = date.today()

        for spec in schools_spec:
            school, school_created = School.objects.get_or_create(
                code=spec["code"],
                defaults={"name": spec["name"], "address": spec["address"]},
            )
            if school_created:
                log(f"School: Created ({spec['name']}, {spec['code']})")
            else:
                log(f"School: Using existing ({spec['name']}, {spec['code']})", level=2)

            subject_code = f"DEMO_{spec['code'][:8].upper()}"

            # All ORM rows for school_data (and User, resolved via tenant+public search_path)
            # must run inside this school's schema.
            with tenant_context(school):
                classroom, _ = ClassRoom.objects.get_or_create(
                    name="10",
                    defaults={"description": ""},
                )
                section_obj, _ = Section.objects.get_or_create(
                    name="Alpha",
                    defaults={"description": ""},
                )
                classroom.sections.add(section_obj)
                subject, _ = Subject.objects.get_or_create(
                    code=subject_code,
                    defaults={"name": "Mathematics"},
                )
                Exam.objects.get_or_create(
                    name="Mid Term",
                    classroom=classroom,
                    defaults={
                        "date": start,
                        "end_date": start + timedelta(days=1),
                    },
                )

                # 2 admins per school
                for i in range(1, 3):
                    username = f"{spec['prefix']}_admin{i}"
                    if User.objects.filter(username=username).exists() and not force:
                        log(f"SchoolAdmin {username} already exists, skipping.", level=2)
                        continue
                    user, created = User.objects.update_or_create(
                        username=username,
                        defaults={
                            "role": User.Roles.ADMIN,
                            "school": school,
                            "is_staff": False,
                            "is_superuser": False,
                            "is_active": True,
                        },
                    )
                    user.set_password("admin123")
                    user.save()
                    log(
                        f"SchoolAdmin: {'Created' if created else 'Updated'} "
                        f"(username: {username}, password: admin123, school: {school.code})"
                    )

                # 5 teachers per school
                for i in range(1, 6):
                    username = f"{spec['prefix']}_teacher{i}"
                    if User.objects.filter(username=username).exists() and not force:
                        log(f"Teacher {username} already exists, skipping.", level=2)
                        continue
                    user, created = User.objects.update_or_create(
                        username=username,
                        defaults={
                            "role": User.Roles.TEACHER,
                            "school": school,
                            "is_staff": False,
                            "is_superuser": False,
                            "is_active": True,
                        },
                    )
                    user.set_password("admin123")
                    user.save()
                    teacher, _ = Teacher.objects.update_or_create(
                        user=user,
                        defaults={"subject": subject},
                    )
                    teacher.subjects.set([subject])
                    teacher.classrooms.set([classroom])
                    log(
                        f"Teacher: {'Created' if created else 'Updated'} "
                        f"(username: {username}, password: admin123, school: {school.code})"
                    )

                # 10 students per school
                for i in range(1, 11):
                    username = f"{spec['prefix']}_student{i}"
                    if User.objects.filter(username=username).exists() and not force:
                        log(f"Student {username} already exists, skipping.", level=2)
                        continue
                    user, created = User.objects.update_or_create(
                        username=username,
                        defaults={
                            "role": User.Roles.STUDENT,
                            "school": school,
                            "is_staff": False,
                            "is_superuser": False,
                            "is_active": True,
                        },
                    )
                    user.set_password("admin123")
                    user.save()
                    Student.objects.update_or_create(
                        user=user,
                        defaults={
                            "roll_number": str(i),
                            "classroom": classroom,
                            "section": section_obj,
                            "admission_number": f"{school.code}-ADM-{i:03d}",
                            "parent_name": f"Parent {i}",
                            "parent_phone": f"99999999{i:02d}",
                        },
                    )
                    log(
                        f"Student: {'Created' if created else 'Updated'} "
                        f"(username: {username}, password: admin123, school: {school.code})",
                        level=2,
                    )

        self.stdout.write(self.style.SUCCESS("Demo users setup complete for two schools."))
