from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import FAQItem, SitePage


@admin.register(SitePage)
class SitePageAdmin(ModelAdmin):
    list_display = ("title", "kind", "slug", "is_published", "updated_at")
    list_filter = ("kind", "is_published")
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(FAQItem)
class FAQItemAdmin(ModelAdmin):
    list_display = ("question", "order", "is_published")
    list_editable = ("order", "is_published")
    search_fields = ("question", "answer")
