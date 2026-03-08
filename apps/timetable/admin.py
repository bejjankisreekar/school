from django.contrib import admin
from .models import TimeSlot, Timetable


@admin.register(TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = ("start_time", "end_time", "is_break", "break_type", "order")


@admin.register(Timetable)
class TimetableAdmin(admin.ModelAdmin):
    list_display = ("classroom", "day_of_week", "time_slot", "subject", "teachers_display")

    def teachers_display(self, obj):
        """
        Show comma-separated list of teachers for this timetable entry.
        """
        names = [t.user.get_full_name() or t.user.username for t in obj.teachers.all()]
        return ", ".join(names) if names else "-"

    teachers_display.short_description = "Teachers"
