from django.db import migrations


def regenerate_without_class(apps, schema_editor):
    Admission = apps.get_model("school_data", "Admission")
    import random
    import re

    rng = random.Random()
    school_code = "chs"
    ssc = re.sub(r"[^a-z0-9]", "", (school_code or "chs").strip().lower())[:6] or "chs"

    def initials(fn: str, ln: str) -> str:
        fn = (fn or "").strip()
        ln = (ln or "").strip()
        if fn and ln:
            return (fn[0] + ln[0]).lower()
        if fn:
            return (fn[:2].ljust(2, fn[:1] or "x")).lower()
        return "xx"

    for adm in Admission.objects.all().only("id", "first_name", "last_name", "created_on").iterator():
        yy = f"{(adm.created_on.year if adm.created_on else 0) % 100:02d}" or "00"
        ii = initials(adm.first_name, adm.last_name)
        for _ in range(50):
            rrr = f"{rng.randint(0, 9_999_999):07d}"
            cand = f"{yy}{ssc}_{rrr}{ii}"
            if not Admission.objects.filter(admission_number=cand).exists():
                Admission.objects.filter(pk=adm.pk).update(admission_number=cand)
                break


class Migration(migrations.Migration):
    dependencies = [
        ("school_data", "0057_admission_number_custom_format"),
    ]

    operations = [
        migrations.RunPython(regenerate_without_class, migrations.RunPython.noop),
    ]

