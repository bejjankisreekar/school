from django.contrib import admin

from .models import ControlCenterSettings


@admin.register(ControlCenterSettings)
class ControlCenterSettingsAdmin(admin.ModelAdmin):
    list_display = ("id", "platform_name", "updated_at", "maintenance_mode")
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        return ControlCenterSettings.objects.count() == 0

    def has_delete_permission(self, request, obj=None):
        return False
