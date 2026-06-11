from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from .constants import COUNTRY_CHOICES, DEFAULT_PAYMENT_TERM
from .models import BuyRequest, Message, RequestItem, TravelPlan

# Text input enhanced by flatpickr (static/vendor/flatpickr). Displays and
# submits dd-Mmm-yyyy (e.g. 19-Jun-2026) on every browser — the native
# <input type="date"> follows the browser locale (e.g. mm/dd/yyyy on US
# English) and can't be forced.
DATE_INPUT = forms.DateInput(
    attrs={"class": "datepicker", "placeholder": "dd-Mmm-yyyy", "autocomplete": "off"},
    format="%d-%b-%Y",
)


class ThousandSeparatorNumberInput(forms.TextInput):
    """Text input that displays thousand separators but submits a clean number.

    Commas are stripped server-side here (belt-and-suspenders with the
    money.js blur formatting), so the bound DecimalField parses correctly even
    if JS is disabled.
    """

    def __init__(self, attrs=None):
        defaults = {"inputmode": "decimal", "autocomplete": "off", "class": "money-input"}
        if attrs:
            defaults.update(attrs)
        super().__init__(defaults)

    def value_from_datadict(self, data, files, name):
        value = super().value_from_datadict(data, files, name)
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return value


class TravelPlanForm(forms.ModelForm):
    from_country = forms.ChoiceField(choices=COUNTRY_CHOICES)
    to_country = forms.ChoiceField(choices=COUNTRY_CHOICES)

    class Meta:
        model = TravelPlan
        fields = [
            "travel_date", "from_city", "from_country", "to_city", "to_country",
            "available_weight_kg", "shipment_currency", "shipment_cost_per_kg",
            "margin_percent", "payment_term",
        ]
        widgets = {
            "travel_date": DATE_INPUT,
            "payment_term": forms.Textarea(attrs={"rows": 3}),
            "shipment_cost_per_kg": ThousandSeparatorNumberInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["travel_date"].input_formats = ["%d-%b-%Y", "%Y-%m-%d"]
        if not self.instance.pk and not self.initial.get("payment_term"):
            self.fields["payment_term"].initial = DEFAULT_PAYMENT_TERM


class BuyRequestForm(forms.ModelForm):
    class Meta:
        model = BuyRequest
        fields = ["buyer_notes"]
        widgets = {"buyer_notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Notes for the traveler (optional)"})}


class ReviewForm(forms.ModelForm):
    """Traveler-side review field: estimated weight of this package (kg).

    Optional here (so a draft can be saved without it); the view requires a
    positive value only when the traveler clicks Accept.
    """

    class Meta:
        model = BuyRequest
        fields = ["estimated_weight_kg"]
        widgets = {
            "estimated_weight_kg": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "inputmode": "decimal", "placeholder": "e.g. 2.5"}
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["estimated_weight_kg"].required = False

    def clean_estimated_weight_kg(self):
        val = self.cleaned_data.get("estimated_weight_kg")
        if val in (None, ""):
            return self.instance.estimated_weight_kg or Decimal("0")
        return val


class RequestItemForm(forms.ModelForm):
    class Meta:
        model = RequestItem
        fields = ["name", "quantity", "photo"]
        widgets = {"name": forms.TextInput(attrs={"placeholder": "Item name"})}

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and hasattr(photo, "size") and photo.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Image must be 2 MB or smaller.")
        if photo and hasattr(photo, "content_type"):
            if photo.content_type not in {"image/png", "image/jpeg", "image/jpg"}:
                raise forms.ValidationError("Only PNG, JPG or JPEG images are allowed.")
        return photo


# Buyer creates up to 10 items.
RequestItemFormSet = inlineformset_factory(
    BuyRequest, RequestItem, form=RequestItemForm,
    extra=3, max_num=10, validate_max=True, can_delete=True,
)

# Traveler sets the estimated cost of each item when reviewing.
ReviewItemFormSet = inlineformset_factory(
    BuyRequest, RequestItem, fields=["estimated_unit_cost"],
    extra=0, can_delete=False,
)

# Traveler records what was actually purchased (incl. actual quantity).
class PurchaseItemForm(forms.ModelForm):
    class Meta:
        model = RequestItem
        fields = ["actual_quantity", "actual_unit_cost", "purchase_photo", "purchase_note"]
        widgets = {
            "purchase_note": forms.TextInput(
                attrs={"placeholder": "e.g. out of stock, bought 2 of 3, substituted"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-fill the actual quantity with the requested quantity for convenience.
        if self.instance and self.instance.pk and not self.instance.actual_quantity:
            self.fields["actual_quantity"].initial = self.instance.quantity


PurchaseItemFormSet = inlineformset_factory(
    BuyRequest, RequestItem, form=PurchaseItemForm, extra=0, can_delete=False,
)


class CustomFareForm(forms.ModelForm):
    """Traveler at arrival: actual (final-measured) weight + custom fare."""

    class Meta:
        model = BuyRequest
        fields = ["actual_weight_kg", "custom_fare_currency", "custom_fare_amount", "custom_fare_proof"]
        widgets = {
            "actual_weight_kg": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "inputmode": "decimal", "placeholder": "e.g. 4.0"}
            ),
        }


class MessageForm(forms.ModelForm):
    class Meta:
        model = Message
        fields = ["body", "photo"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 2, "placeholder": "Write a message…"}),
        }

    def clean_body(self):
        body = (self.cleaned_data.get("body") or "").strip()
        if not body:
            raise forms.ValidationError("Message cannot be empty.")
        return body

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and hasattr(photo, "size") and photo.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Image must be 5 MB or smaller.")
        return photo


class RefundBankForm(forms.ModelForm):
    """Buyer's bank details for receiving an overpaid refund."""

    class Meta:
        model = BuyRequest
        fields = ["refund_bank_name", "refund_account_no", "refund_account_name"]
        labels = {
            "refund_bank_name": "Bank name",
            "refund_account_no": "Account number",
            "refund_account_name": "Account holder name",
        }
        widgets = {
            "refund_bank_name": forms.TextInput(attrs={"placeholder": "e.g. PT Bank OCBC NISP, Tbk"}),
            "refund_account_no": forms.TextInput(attrs={"placeholder": "Your account number"}),
            "refund_account_name": forms.TextInput(attrs={"placeholder": "Name on the account"}),
        }

    def clean(self):
        cleaned = super().clean()
        for f in ("refund_bank_name", "refund_account_no", "refund_account_name"):
            if not (cleaned.get(f) or "").strip():
                self.add_error(f, "Required.")
        return cleaned
