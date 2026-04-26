from django.contrib import admin
from .models import (
    AcademicYear,
    ClassRoom,
    Section,
    Student,
    StudentDocument,
    Teacher,
    Subject,
    Exam,
    ExamMarkComponent,
    ClassSectionSubjectTeacher,
    Attendance,
    Marks,
    Grade,
    Homework,
    FeeType,
    FeeStructure,
    Fee,
    Payment,
    PaymentBatch,
    PaymentBatchTender,
    PaymentReceipt,
    Parent,
    StudentParent,
    Announcement,
    StaffAttendance,
    SupportTicket,
    SupportMessage,
    InventoryItem,
    InventoryTransaction,
    Purchase,
    Invoice,
    InvoiceItem,
    HolidayCalendar,
    HolidayEvent,
    WorkingSundayOverride,
)


class HolidayEventInline(admin.TabularInline):
    model = HolidayEvent
    extra = 0
    fields = ("name", "holiday_type", "start_date", "applies_to", "recurring_yearly", "description")


class WorkingSundayOverrideInline(admin.TabularInline):
    model = WorkingSundayOverride
    extra = 0


@admin.register(HolidayCalendar)
class HolidayCalendarAdmin(admin.ModelAdmin):
    list_display = ("academic_year", "name", "is_published", "use_split_calendars", "published_at")
    list_filter = ("is_published", "use_split_calendars")
    inlines = (HolidayEventInline, WorkingSundayOverrideInline)


@admin.register(HolidayEvent)
class HolidayEventAdmin(admin.ModelAdmin):
    list_display = ("name", "calendar", "holiday_type", "start_date", "applies_to", "recurring_yearly")
    list_filter = ("holiday_type", "applies_to", "recurring_yearly")
    exclude = ("end_date",)


@admin.register(WorkingSundayOverride)
class WorkingSundayOverrideAdmin(admin.ModelAdmin):
    list_display = ("calendar", "work_date", "applies_to", "note")


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(ClassRoom)
class ClassRoomAdmin(admin.ModelAdmin):
    list_display = ("name", "grade_order", "academic_year", "capacity")
    list_filter = ("academic_year",)
    search_fields = ("name",)


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "description", "created_on")
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "display_order", "created_on")
    search_fields = ("name", "code")
    ordering = ("display_order", "name")


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ("user", "employee_id", "phone_number")
    search_fields = ("user__username", "employee_id")


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("user", "classroom", "section", "roll_number", "admission_number")
    list_filter = ("classroom", "section")
    search_fields = ("user__username", "roll_number", "admission_number")


class ExamMarkComponentInline(admin.TabularInline):
    model = ExamMarkComponent
    extra = 0
    fields = ("component_name", "max_marks", "sort_order")


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ("name", "class_name", "section", "date", "created_by")
    list_filter = ("class_name", "section", "date")
    inlines = (ExamMarkComponentInline,)


@admin.register(ClassSectionSubjectTeacher)
class ClassSectionSubjectTeacherAdmin(admin.ModelAdmin):
    list_display = ("class_obj", "section", "subject", "teacher")
    list_filter = ("class_obj", "section", "subject")
    search_fields = ("subject__name", "teacher__user__username", "teacher__user__first_name", "teacher__user__last_name")

@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("student", "date", "status")
    list_filter = ("date", "status")


@admin.register(Marks)
class MarksAdmin(admin.ModelAdmin):
    list_display = ("student", "subject", "exam", "marks_obtained", "total_marks")
    list_filter = ("subject", "exam")


@admin.register(Homework)
class HomeworkAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "homework_type",
        "status",
        "priority",
        "subject",
        "assigned_date",
        "due_date",
        "max_marks",
        "assigned_by",
    )
    list_filter = ("homework_type", "status", "priority", "subject", "due_date")
    search_fields = ("title", "description", "instructions")


@admin.register(FeeType)
class FeeTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "created_on")
    list_filter = ("is_active",)


@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = ("fee_type", "classroom", "academic_year", "amount")
    list_filter = ("fee_type", "academic_year")


@admin.register(Fee)
class FeeAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "fee_structure",
        "amount",
        "concession_percent",
        "concession_fixed",
        "due_date",
        "status",
    )
    list_filter = ("status", "concession_kind")


class PaymentBatchTenderInline(admin.TabularInline):
    model = PaymentBatchTender
    extra = 0


@admin.register(PaymentBatch)
class PaymentBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "total_amount", "payment_date", "payment_method", "created_at")
    list_filter = ("payment_date", "payment_method")
    raw_id_fields = ("student", "academic_year", "received_by")
    search_fields = ("receipt_number", "transaction_reference", "notes")
    inlines = [PaymentBatchTenderInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("fee", "batch", "amount", "payment_date", "payment_method", "transaction_reference")


@admin.register(Parent)
class ParentAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone")


@admin.register(StudentParent)
class StudentParentAdmin(admin.ModelAdmin):
    list_display = ("student", "parent")


@admin.register(StaffAttendance)
class StaffAttendanceAdmin(admin.ModelAdmin):
    list_display = ("teacher", "date", "status")


@admin.register(StudentDocument)
class StudentDocumentAdmin(admin.ModelAdmin):
    list_display = ("student", "doc_type", "title", "uploaded_at")
    list_filter = ("doc_type",)


@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
    list_display = ("name", "min_percentage", "max_percentage")


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ("payment", "receipt_number", "generated_at")


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "published_at", "is_pinned")


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("subject", "priority", "status")


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ("ticket", "is_staff_reply", "created_at")


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    list_display = ("inventory_item", "transaction_type", "quantity", "created_at")
