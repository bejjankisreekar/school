from django.contrib import admin
from .models import (
    School,
    AcademicYear,
    ClassRoom,
    Section,
    Student,
    Exam,
    Subject,
    Teacher,
    Attendance,
    Marks,
    Homework,
)


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "start_date", "end_date", "is_active")
    list_filter = ("school", "is_active")
    search_fields = ("name",)


@admin.register(ClassRoom)
class ClassRoomAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "academic_year", "capacity")
    list_filter = ("school", "academic_year")
    search_fields = ("name",)


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("name", "classroom", "class_teacher", "capacity", "school")
    list_filter = ("school",)
    search_fields = ("name",)


admin.site.register(School)
admin.site.register(Student)
admin.site.register(Exam)
admin.site.register(Subject)
admin.site.register(Teacher)
admin.site.register(Attendance)
admin.site.register(Marks)
admin.site.register(Homework)
