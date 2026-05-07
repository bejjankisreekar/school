from django.contrib import admin

from .models import PlatformMessage, PlatformMessageThread


@admin.register(PlatformMessageThread)
class PlatformMessageThreadAdmin(admin.ModelAdmin):
    list_display = ("id", "school", "updated_at", "archived_by_superadmin", "archived_by_school", "pinned_at")
    search_fields = ("school__name", "school__code")


@admin.register(PlatformMessage)
class PlatformMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "thread", "sender_role", "sender", "created_at", "read_at")
    list_filter = ("sender_role",)
    search_fields = ("body",)
