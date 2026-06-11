from django import forms

from .models import ContactMessage


class ContactForm(forms.ModelForm):
    # Honeypot: real users never see/fill this; bots usually do. Submissions with
    # it populated are dropped silently in the view.
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
    )

    class Meta:
        model = ContactMessage
        fields = ["name", "email", "topic", "message"]
        widgets = {
            "message": forms.Textarea(attrs={"rows": 5, "placeholder": "How can we help?"}),
        }
