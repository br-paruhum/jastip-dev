from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import NotificationLog


@admin.register(NotificationLog)
class NotificationLogAdmin(ModelAdmin):
    list_display = ("created_at", "channel", "to_address", "event", "status")
    list_filter = ("channel", "status", "event")
    search_fields = ("to_address", "subject", "body")
    readonly_fields = ("created_at",)
