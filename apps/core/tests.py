from datetime import date

from django.test import TestCase
from unittest.mock import patch

from apps.core.admissions_utils import (
    extract_grade_2digit,
    generate_admission_number,
    student_initials,
)

from apps.school_data.models import Admission, ClassRoom


class AdmissionNumberUtilsTests(TestCase):
    def test_grade_formatting(self):
        self.assertEqual(extract_grade_2digit("Class 1"), "01")
        self.assertEqual(extract_grade_2digit("Grade 5"), "05")
        self.assertEqual(extract_grade_2digit("10"), "10")
        self.assertEqual(extract_grade_2digit("NoClass"), "00")

    def test_initials_extraction(self):
        self.assertEqual(student_initials("Varun", "Chary"), "vc")
        self.assertEqual(student_initials("Sreekar", "Bejjanki"), "sb")
        self.assertEqual(student_initials("Sreekar", ""), "sr")
        self.assertEqual(student_initials("", ""), "xx")

    def test_year_generation(self):
        n = generate_admission_number(
            "Varun",
            "Chary",
            "10",
            "chs",
            today=date(2026, 4, 14),
        )
        self.assertTrue(n.startswith("26chs_"))

    def test_school_code_normalization(self):
        n = generate_admission_number(
            "Varun",
            "Chary",
            "10",
            "CHS!!",
            today=date(2026, 4, 14),
        )
        self.assertTrue(n.startswith("26chs_"))

    def test_duplicate_prevention_in_model_save(self):
        c = ClassRoom.objects.create(name="Grade 10")

        # First admission with a forced number
        a1 = Admission.objects.create(
            application_id="ADM-2026-9999",
            first_name="Varun",
            last_name="Chary",
            applying_for_class=c,
            admission_number="26chs_0000001vc",
        )

        # Second admission: patch generator to collide once, then return a unique value
        with patch("apps.core.admissions_utils.generate_admission_number") as gen:
            gen.side_effect = ["26chs_0000001vc", "26chs_0000002vc"]
            a2 = Admission(
                application_id="ADM-2026-9998",
                first_name="Varun",
                last_name="Chary",
                applying_for_class=c,
            )
            a2.save()

        self.assertNotEqual(a2.admission_number, a1.admission_number)
        self.assertEqual(a2.admission_number, "26chs_0000002vc")

from django.test import TestCase

# Create your tests here.
