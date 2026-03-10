from django.urls import path
from . import views

app_name = "timetable"

urlpatterns = [
    path("school/timeslots/", views.school_timeslots, name="school_timeslots"),
    path("school/timeslots/<int:slot_id>/update/", views.school_timeslot_update, name="school_timeslot_update"),
    path("school/timeslots/<int:slot_id>/delete/", views.school_timeslot_delete, name="school_timeslot_delete"),
    path("school/timetable/", views.school_timetable_index, name="school_timetable_index"),
    path("school/timetable/<int:classroom_id>/", views.school_timetable, name="school_timetable"),
    path("school/timetable/<int:classroom_id>/copy-monday/", views.school_timetable_copy_monday, name="school_timetable_copy_monday"),
    path("school/timetable/<int:classroom_id>/duplicate/", views.school_timetable_duplicate, name="school_timetable_duplicate"),
    path("school/timetable/<int:classroom_id>/print/", views.school_timetable_print, name="school_timetable_print"),
    path("school/timetable/<int:classroom_id>/pdf/", views.school_timetable_pdf, name="school_timetable_pdf"),
    path("student/timetable/", views.student_timetable, name="student_timetable"),
    path("teacher/timetable/", views.teacher_timetable, name="teacher_timetable"),
]
