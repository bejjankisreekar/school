from django.db import models
from django.core.exceptions import ValidationError


class ScheduleProfile(models.Model):
    """Tenant-scoped schedule profile (e.g., Default, Exam week, Saturday). Each profile has its own time slots."""

    name = models.CharField(max_length=80, db_index=True)
    description = models.TextField(blank=True)
    academic_year = models.ForeignKey(
        "school_data.AcademicYear",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schedule_profiles",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    default_start_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Suggested day start when generating or documenting this profile.",
    )
    default_end_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Suggested day end when generating or documenting this profile.",
    )
    total_periods = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Target number of teaching periods (optional reference).",
    )
    break_enabled = models.BooleanField(
        default=True,
        help_text="Whether this profile typically includes break rows (hint for admins).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_active", "name"]

    def __str__(self) -> str:
        return self.name


class TimeSlot(models.Model):
    """Tenant model - schema defines school."""
    class SlotType(models.TextChoices):
        TEACHING = "TEACHING", "Teaching period"
        STUDY = "STUDY", "Study hours"
        READING = "READING", "Reading"
        GAMES = "GAMES", "Games"
        PRACTICAL = "PRACTICAL", "Practical / Lab"
        DOUBT = "DOUBT", "Doubt Clearing Session"
        PROJECT = "PROJECT", "Project Work"
        COMPUTER_LAB = "COMPUTER_LAB", "Computer Lab"
        OTHER = "OTHER", "Other"
        BREAK = "BREAK", "Break"

    class BreakType(models.TextChoices):
        NONE = "NONE", "None"
        SHORT_BREAK = "SHORT_BREAK", "Short Break"
        LUNCH_BREAK = "LUNCH_BREAK", "Lunch Break"

    profile = models.ForeignKey(
        ScheduleProfile,
        on_delete=models.CASCADE,
        related_name="time_slots",
        null=True,
        blank=True,
    )
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_break = models.BooleanField(default=False)
    slot_type = models.CharField(
        max_length=20,
        choices=SlotType.choices,
        default=SlotType.TEACHING,
    )
    slot_label = models.CharField(max_length=60, blank=True, default="")
    break_type = models.CharField(
        max_length=20,
        choices=BreakType.choices,
        default=BreakType.NONE,
    )
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["order", "start_time"]

    def __str__(self) -> str:
        if self.is_break:
            return f"{self.start_time}–{self.end_time} ({self.get_break_type_display()})"
        return f"{self.start_time}–{self.end_time}"

    def clean(self):
        if self.is_break and self.break_type == self.BreakType.NONE:
            self.break_type = self.BreakType.SHORT_BREAK

    def save(self, *args, **kwargs):
        if not self.is_break:
            self.break_type = self.BreakType.NONE
            if self.slot_type == self.SlotType.BREAK:
                self.slot_type = self.SlotType.TEACHING
        elif self.is_break and self.break_type == self.BreakType.NONE:
            self.break_type = self.BreakType.SHORT_BREAK
        if self.is_break:
            self.slot_type = self.SlotType.BREAK
            self.slot_label = ""
        if self.slot_type != self.SlotType.OTHER:
            self.slot_label = ""
        super().save(*args, **kwargs)


class Timetable(models.Model):
    class DayOfWeek(models.IntegerChoices):
        MONDAY = 1, "Monday"
        TUESDAY = 2, "Tuesday"
        WEDNESDAY = 3, "Wednesday"
        THURSDAY = 4, "Thursday"
        FRIDAY = 5, "Friday"
        SATURDAY = 6, "Saturday"

    profile = models.ForeignKey(
        ScheduleProfile,
        on_delete=models.CASCADE,
        related_name="entries",
        null=True,
        blank=True,
    )
    classroom = models.ForeignKey(
        "school_data.ClassRoom",
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
        "school_data.Subject",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="timetable_entries",
    )
    teachers = models.ManyToManyField(
        "school_data.Teacher",
        blank=True,
        related_name="timetable_entries",
    )

    class Meta:
        unique_together = ("classroom", "profile", "day_of_week", "time_slot")

    def __str__(self) -> str:
        return f"{self.classroom} {self.get_day_of_week_display()} {self.time_slot}"

    def clean(self):
        if self.time_slot and self.time_slot.is_break:
            if self.subject_id:
                raise ValidationError("Subject must be null for break slots.")
            # ManyToMany is only available after save; enforce when possible.
            if self.pk and self.teachers.exists():
                raise ValidationError("Teachers must be empty for break slots.")
