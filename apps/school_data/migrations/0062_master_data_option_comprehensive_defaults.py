from django.db import migrations


def seed_comprehensive_defaults(apps, schema_editor):
    MasterDataOption = apps.get_model("school_data", "MasterDataOption")
    from apps.school_data.master_data_defaults import seed_master_data_options

    seed_master_data_options(MasterDataOption)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("school_data", "0061_dropdownmaster_audit_fix"),
    ]

    operations = [
        migrations.RunPython(seed_comprehensive_defaults, reverse_code=noop_reverse),
    ]
