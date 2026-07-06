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

    # Sensitive profile PII (delivery addresses + bank accounts). Shown only to
    # superusers, on the change page only. Read-only by default, EXCEPT the
    # buyer's invoice/customs address, which superusers may correct. Never
    # expose password or the phone OTP here.
    SENSITIVE_FIELDS = (
        "buyer_destination_city", "buyer_invoice_address", "buyer_bank_details",
        "traveler_destination_city", "traveler_address", "traveler_bank_details",
    )
    EDITABLE_SENSITIVE_FIELDS = ("buyer_invoice_address",)

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is not None and request.user.is_superuser:
            fieldsets = tuple(fieldsets) + (
                ("Addresses & Bank details (read-only, except Invoice Address)",
                 {"fields": self.SENSITIVE_FIELDS}),
            )
        return fieldsets

    def get_readonly_fields(self, request, obj=None):
        readonly = tuple(super().get_readonly_fields(request, obj))
        if request.user.is_superuser:
            readonly += tuple(
                f for f in self.SENSITIVE_FIELDS
                if f not in self.EDITABLE_SENSITIVE_FIELDS
            )
        return readonly
