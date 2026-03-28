"""Shorten subject codes to max 20 chars before AlterField (matches ERP spec)."""

from django.db import migrations, models


def _shorten_subject_codes(apps, schema_editor):
    Subject = apps.get_model("school_data", "Subject")
    max_len = 20
    for s in Subject.objects.order_by("id"):
        raw = (s.code or "").strip() or f"AUTO{s.pk}"
        if len(raw) <= max_len:
            if s.code != raw:
                s.code = raw
                s.save(update_fields=["code"])
            continue
        base = raw[:max_len]
        candidate = base
        n = 0
        while (
            Subject.objects.exclude(pk=s.pk)
            .filter(code=candidate)
            .exists()
        ):
            n += 1
            suffix = f"-{n}"
            candidate = (raw[: max_len - len(suffix)] + suffix)[:max_len]
        s.code = candidate
        s.save(update_fields=["code"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0015_subject_master_unique_code"),
    ]

    operations = [
        migrations.RunPython(_shorten_subject_codes, noop_reverse),
        migrations.AlterField(
            model_name="subject",
            name="code",
            field=models.CharField(
                db_index=True,
                help_text="Short unique code, e.g. MATH01.",
                max_length=20,
                unique=True,
            ),
        ),
    ]
