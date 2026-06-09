from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Post


@admin.register(Post)
class PostAdmin(ModelAdmin):
    list_display = ("title", "status", "author", "published_at", "updated_at")
    list_filter = ("status", "published_at")
    search_fields = ("title", "excerpt", "body")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("author",)
    date_hierarchy = "published_at"
