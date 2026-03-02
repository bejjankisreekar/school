"""
Django management command to create demo users.
Run: python manage.py create_demo_users
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.core.models import School, Student, Teacher, Subject, ClassRoom

User = get_user_model()


class Command(BaseCommand):
    help = "Create demo users: SuperAdmin, School, SchoolAdmin, Teacher, Student"

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

        # 2. School
        school, school_created = School.objects.get_or_create(
            code="GVS001",
            defaults={"name": "Green Valley School", "address": ""},
        )
        if school_created:
            log("School: Created (Green Valley School, GVS001)")

        # 3. School Admin
        if User.objects.filter(username="schooladmin").exists() and not force:
            log("SchoolAdmin already exists, skipping.")
        else:
            user, created = User.objects.update_or_create(
                username="schooladmin",
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
            log(f"SchoolAdmin: {'Created' if created else 'Updated'} (username: schooladmin, password: admin123)")

        # 4. ClassRoom: 10-A
        classroom, _ = ClassRoom.objects.get_or_create(
            school=school,
            name="10",
            section="A",
            defaults={},
        )

        # 5. Subject: Mathematics
        subject, _ = Subject.objects.get_or_create(
            school=school,
            name="Mathematics",
            defaults={},
        )

        # 6. Teacher
        if User.objects.filter(username="teacher1").exists() and not force:
            log("Teacher already exists, skipping.")
        else:
            user, created = User.objects.update_or_create(
                username="teacher1",
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
            log(f"Teacher: {'Created' if created else 'Updated'} (username: teacher1, password: admin123, subject: Mathematics)")

        # 7. Student
        if User.objects.filter(username="student1").exists() and not force:
            log("Student already exists, skipping.")
        else:
            user, created = User.objects.update_or_create(
                username="student1",
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
                    "grade": "10",
                    "section": "A",
                    "roll_number": "23",
                    "classroom": classroom,
                },
            )
            log(f"Student: {'Created' if created else 'Updated'} (username: student1, password: admin123, grade: 10, section: A, roll: 23)")

        self.stdout.write(self.style.SUCCESS("Demo users setup complete."))
