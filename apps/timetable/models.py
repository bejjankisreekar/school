from django.db import models
from django.core.exceptions import ValidationError


class TimeSlot(models.Model):
    class BreakType(models.TextChoices):
        NONE = "NONE", "None"
        SHORT_BREAK = "SHORT_BREAK", "Short Break"
        LUNCH_BREAK = "LUNCH_BREAK", "Lunch Break"

    start_time = models.TimeField()
    end_time = models.TimeField()
    is_break = models.BooleanField(default=False)
    break_type = models.CharField(
        max_length=20,
        choices=BreakType.choices,
        default=BreakType.NONE,
    )
    order = models.IntegerField(default=0)
    school = models.ForeignKey(
        "core.School",
        on_delete=models.CASCADE,
        related_name="time_slots",
        to_field="code",
    )

    class Meta:
        ordering = ["school", "order", "start_time"]

    def __str__(self) -> str:
        if self.is_break:
            return f"{self.start_time}–{self.end_time} ({self.get_break_type_display()})"
        return f"{self.start_time}–{self.end_time}"

    def clean(self):
        if self.is_break and self.break_type == self.BreakType.NONE:
            self.break_type = self.BreakType.SHORT_BREAK


class Timetable(models.Model):
    class DayOfWeek(models.IntegerChoices):
        MONDAY = 1, "Monday"
        TUESDAY = 2, "Tuesday"
        WEDNESDAY = 3, "Wednesday"
        THURSDAY = 4, "Thursday"
        FRIDAY = 5, "Friday"
        SATURDAY = 6, "Saturday"

    classroom = models.ForeignKey(
        "core.ClassRoom",
        on_delete=models.CASCADE,
        related_name="timetables",
    )
    day_of_week = models.PositiveSmallIntegerField(choices=DayOfWeek.choices)
    time_slot = models.ForeignKey(
        TimeSlot,
        on_delete=models.CASCADE,
        related_name="timetable_entries",
    )
    subject = models.ForeignKey(
        "core.Subject",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="timetable_entries",
    )
    teachers = models.ManyToManyField(
        "core.Teacher",
        blank=True,
        related_name="timetable_entries",
    )
    school = models.ForeignKey(
        "core.School",
        on_delete=models.CASCADE,
        related_name="timetable_entries",
        to_field="code",
    )

    class Meta:
        unique_together = ("classroom", "day_of_week", "time_slot")

    def __str__(self) -> str:
        return f"{self.classroom} {self.get_day_of_week_display()} {self.time_slot}"

    def clean(self):
        if self.time_slot and self.time_slot.is_break:
            if self.subject_id:
                raise ValidationError("Subject must be null for break slots.")
            # ManyToMany is only available after save; enforce when possible.
            if self.pk and self.teachers.exists():
                raise ValidationError("Teachers must be empty for break slots.")
