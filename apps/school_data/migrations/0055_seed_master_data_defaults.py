from django.db import migrations


DEFAULTS = {
    "gender": ["Male", "Female", "Other"],
    "blood_group": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"],
}


def seed_defaults(apps, schema_editor):
    MasterDataOption = apps.get_model("school_data", "MasterDataOption")
    for key, names in DEFAULTS.items():
        existing = set(
            MasterDataOption.objects.filter(key=key).values_list("name_normalized", flat=True)
        )
        for n in names:
            nn = n.strip().lower()
            if nn in existing:
                continue
            MasterDataOption.objects.create(
                key=key,
                name=n.strip(),
                name_normalized=nn,
                is_active=True,
            )


def noop_reverse(apps, schema_editor):
    # Keep admin-added values; do not delete on rollback.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("school_data", "0054_master_data_option"),
    ]

    operations = [
        migrations.RunPython(seed_defaults, reverse_code=noop_reverse),
    ]

