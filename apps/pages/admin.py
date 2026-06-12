from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import ContactMessage, FAQItem, Promo, SitePage, SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin):
    list_display = ("__str__", "updated_at")

    def has_add_permission(self, request):
        # Singleton: only allow creating the row if none exists yet.
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ContactMessage)
class ContactMessageAdmin(ModelAdmin):
    list_display = ("created_at", "topic", "name", "email", "handled")
    list_editable = ("handled",)
    list_filter = ("topic", "handled", "created_at")
    search_fields = ("name", "email", "message")
    readonly_fields = ("name", "email", "topic", "message", "created_at")


@admin.register(SitePage)
class SitePageAdmin(ModelAdmin):
    list_display = ("title", "kind", "slug", "is_published", "updated_at")
    list_filter = ("kind", "is_published")
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(Promo)
class PromoAdmin(ModelAdmin):
    list_display = ("title", "badge", "is_active", "order", "url", "updated_at")
    list_editable = ("is_active", "order")
    list_filter = ("is_active",)
    search_fields = ("title", "body")


@admin.register(FAQItem)
class FAQItemAdmin(ModelAdmin):
    list_display = ("question", "order", "is_published")
    list_editable = ("order", "is_published")
    search_fields = ("question", "answer")
