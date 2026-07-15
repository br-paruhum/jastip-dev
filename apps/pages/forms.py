from django import forms

from .models import ContactMessage


class CarrierLeadForm(forms.Form):
    """Opt-in capture for travellers who fly a route regularly (e.g. Jakarta↔Germany).
    Not a ModelForm: the route-specific fields are folded into a ContactMessage in the
    view, so leads land in the existing Contact inbox with no extra DB columns."""

    website = forms.CharField(  # honeypot — see ContactForm
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
    )
    name = forms.CharField(max_length=120)
    email = forms.EmailField()
    home_city = forms.CharField(
        max_length=120, label="Your home city in Germany",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Berlin, München, Frankfurt"}),
    )
    frequency = forms.ChoiceField(
        label="How often do you fly Jakarta ↔ Germany?",
        choices=[
            ("", "Select…"),
            ("monthly", "About once a month"),
            ("quarterly", "Every few months"),
            ("twice_a_year", "Twice a year"),
            ("yearly", "About once a year"),
            ("occasionally", "Occasionally / not sure yet"),
        ],
    )
    note = forms.CharField(
        required=False, label="Anything else? (optional)",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Next trip dates, spare kg, questions…"}),
    )


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
