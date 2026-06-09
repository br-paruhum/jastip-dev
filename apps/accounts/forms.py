from django import forms

from .models import User


class ProfileForm(forms.ModelForm):
    """Name + WhatsApp number, collected on the profile page after first login."""

    class Meta:
        model = User
        fields = ["full_name", "phone_country_code", "phone_number"]
        widgets = {
            "full_name": forms.TextInput(attrs={"placeholder": "Your full name", "autocomplete": "name"}),
            "phone_country_code": forms.TextInput(attrs={"placeholder": "+62", "class": "country-code"}),
            "phone_number": forms.TextInput(attrs={"placeholder": "81234567890", "inputmode": "numeric"}),
        }

    def clean_phone_country_code(self):
        code = (self.cleaned_data["phone_country_code"] or "").strip()
        if code and not code.startswith("+"):
            code = "+" + code
        if not code.lstrip("+").isdigit():
            raise forms.ValidationError("Country code must be like +62.")
        return code

    def clean_phone_number(self):
        number = (self.cleaned_data["phone_number"] or "").strip().replace(" ", "")
        if number and not number.isdigit():
            raise forms.ValidationError("Phone number must contain digits only.")
        return number

    def save(self, commit=True):
        user = super().save(commit=False)
        # Changing the number invalidates a prior verification.
        if "phone_number" in self.changed_data or "phone_country_code" in self.changed_data:
            user.phone_verified = False
        if commit:
            user.save()
        return user


class OTPForm(forms.Form):
    code = forms.CharField(max_length=6, min_length=6, widget=forms.TextInput(
        attrs={"placeholder": "6-digit code", "inputmode": "numeric", "autocomplete": "one-time-code"}
    ))
