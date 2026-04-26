from django.db import migrations, models


def backfill_admission_numbers(apps, schema_editor):
    Admission = apps.get_model("school_data", "Admission")
    # Import in-migration safe: keep it simple here to avoid import issues.
    import re
    import random
    from datetime import date

    first_int = re.compile(r"(\d+)")

    def grade2(name: str) -> str:
        m = first_int.search(name or "")
        if not m:
            return "00"
        try:
            n = int(m.group(1))
        except Exception:
            return "00"
        return f"{max(0, min(n, 99)):02d}"

    def initials(fn: str, ln: str) -> str:
        fn = (fn or "").strip()
        ln = (ln or "").strip()
        if fn and ln:
            return (fn[0] + ln[0]).lower()
        if fn:
            return (fn[:2].ljust(2, fn[:1] or "x")).lower()
        return "xx"

    yy = f"{date.today().year % 100:02d}"
    school_code = "chs"
    rng = random.Random()

    for adm in Admission.objects.filter(admission_number="").iterator():
        cc = grade2(getattr(adm, "applying_for_class", None).name if getattr(adm, "applying_for_class", None) else "")
        ii = initials(adm.first_name, adm.last_name)
        for _ in range(50):
            rrr = f"{rng.randint(0, 9_999_999):07d}"
            cand = f"{yy}{cc}{school_code}_{rrr}{ii}"
            if not Admission.objects.filter(admission_number=cand).exists():
                Admission.objects.filter(pk=adm.pk).update(admission_number=cand)
                break


class Migration(migrations.Migration):
    dependencies = [
        ("school_data", "0056_admissions_management_module"),
    ]

    operations = [
        migrations.AddField(
            model_name="admission",
            name="admission_number",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                editable=False,
                help_text="Auto generated like 2610chs_2536486vc",
                max_length=40,
                unique=True,
            ),
        ),
        migrations.RunPython(backfill_admission_numbers, migrations.RunPython.noop),
    ]

