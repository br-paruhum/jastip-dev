from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    ordering = ("email",)
    list_display = ("email", "full_name", "phone_verified", "is_staff", "date_joined")
    list_filter = ("is_staff", "is_superuser", "phone_verified", "is_active")
    search_fields = ("email", "full_name", "phone_number")
    readonly_fields = ("last_login", "date_joined")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile (private)", {"fields": ("full_name", "phone_country_code", "phone_number", "phone_verified")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "usable_password", "password1", "password2"),
        }),
    )
