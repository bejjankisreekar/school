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
    ClassSectionSubjectTeacher,
    Attendance,
    Marks,
    Grade,
    Homework,
    FeeType,
    FeeStructure,
    Fee,
    Payment,
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
)


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(ClassRoom)
class ClassRoomAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "capacity")
    list_filter = ("academic_year",)
    search_fields = ("name",)


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "description", "created_on")
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "classroom", "teacher", "academic_year")
    list_filter = ("academic_year",)
    search_fields = ("name", "code")


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ("user", "employee_id", "phone_number")
    search_fields = ("user__username", "employee_id")


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("user", "classroom", "section", "roll_number", "admission_number")
    list_filter = ("classroom", "section")
    search_fields = ("user__username", "roll_number", "admission_number")


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ("name", "class_name", "section", "date", "created_by")
    list_filter = ("class_name", "section", "date")


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
    list_display = ("title", "subject", "teacher", "due_date")
    list_filter = ("subject", "due_date")


@admin.register(FeeType)
class FeeTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code")


@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = ("fee_type", "classroom", "academic_year", "amount")
    list_filter = ("fee_type", "academic_year")


@admin.register(Fee)
class FeeAdmin(admin.ModelAdmin):
    list_display = ("student", "fee_structure", "amount", "due_date", "status")
    list_filter = ("status",)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("fee", "amount", "payment_date", "payment_method")


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
