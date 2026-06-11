from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import ContactMessage, FAQItem, Promo, SitePage


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
