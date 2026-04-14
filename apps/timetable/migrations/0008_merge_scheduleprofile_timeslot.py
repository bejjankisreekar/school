# Merge parallel 0006 branches (schedule profile fields vs timeslot slot_type/slot_label).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("timetable", "0006_scheduleprofile_extended"),
        ("timetable", "0007_timeslot_slot_label"),
    ]

    operations = []
