from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from .constants import COUNTRY_CHOICES, Currency
from .models import BuyRequest, Message, RequestItem, TravelerOffer, TravelPlan

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

    carrier_only = forms.TypedChoiceField(
        choices=[("0", "Proxy Buyer"), ("1", "Carrier Only")],
        coerce=lambda v: v == "1",
        widget=forms.Select(),
        initial="0",
        required=False,
        label="Service",
        help_text="Proxy Buyer: you buy the items abroad for the buyer. "
                  "Carrier: you only carry cargo the buyer already has — no buying.",
    )

    class Meta:
        model = TravelPlan
        fields = [
            "travel_date", "travel_time", "from_city", "from_country", "to_city", "to_country",
            "available_weight_kg", "shipment_cost_per_kg",
            "margin_percent", "carrier_only",
        ]
        widgets = {
            "travel_date": DATE_INPUT,
            "travel_time": forms.TimeInput(attrs={"type": "time"}),
            "shipment_cost_per_kg": ThousandSeparatorNumberInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["travel_date"].input_formats = ["%d-%b-%Y", "%Y-%m-%d"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("carrier_only"):
            cleaned["margin_percent"] = Decimal("0")
        return cleaned


class BuyRequestForm(forms.ModelForm):
    class Meta:
        model = BuyRequest
        fields = ["estimated_weight_kg", "buyer_notes"]
        widgets = {
            "estimated_weight_kg": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal", "placeholder": "e.g. 2.5"}
            ),
            "buyer_notes": forms.Textarea(attrs={"rows": 4, "placeholder": "Notes for the traveler (optional)"}),
        }


class OrderForm(forms.ModelForm):
    """Buyer-first 'place an order' form — no traveler yet (see
    PLAN-buyer-first-orders.md §3-4). Posts a BuyRequest with plan=None."""

    from_country = forms.ChoiceField(choices=COUNTRY_CHOICES)
    to_country = forms.ChoiceField(choices=COUNTRY_CHOICES)
    partial_allowed = forms.TypedChoiceField(
        choices=[("1", "Partial Allowed"), ("0", "Partial Not Allowed")],
        coerce=lambda v: v == "1",
        widget=forms.Select(),
        initial="0",
    )

    class Meta:
        model = BuyRequest
        fields = [
            "from_city", "from_country", "to_city", "to_country",
            "to_address", "to_postal_code",
            "max_acceptable_date", "bid_weight_kg", "bid_cost_per_kg",
            "partial_allowed", "buyer_notes",
        ]
        widgets = {
            "to_address": forms.Textarea(attrs={"rows": 2, "placeholder": "Destination delivery address"}),
            "max_acceptable_date": DATE_INPUT,
            "bid_weight_kg": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal",
                       "placeholder": "e.g. 2.5", "style": "text-align:right"}
            ),
            "bid_cost_per_kg": ThousandSeparatorNumberInput(attrs={"style": "text-align:right"}),
            "buyer_notes": forms.Textarea(attrs={"rows": 4, "placeholder": "Notes for the traveler (optional)"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["max_acceptable_date"].input_formats = ["%d-%b-%Y", "%Y-%m-%d"]
        # Start the bid fields blank instead of pre-filling the model's 0
        # default — otherwise typing "5" into the leading 0 produces "50".
        self.fields["bid_weight_kg"].initial = None
        self.fields["bid_cost_per_kg"].initial = None

    def clean_bid_weight_kg(self):
        val = self.cleaned_data.get("bid_weight_kg")
        if not val or val <= 0:
            raise forms.ValidationError("Enter the package weight (kg).")
        return val

    def clean_bid_cost_per_kg(self):
        val = self.cleaned_data.get("bid_cost_per_kg")
        if not val or val <= 0:
            raise forms.ValidationError("Enter your opening price per kg.")
        return val

    def clean_max_acceptable_date(self):
        val = self.cleaned_data.get("max_acceptable_date")
        if not val:
            raise forms.ValidationError("Set a deadline for receiving offers.")
        if val <= timezone.now().date():
            raise forms.ValidationError("Deadline must be in the future.")
        return val


class TravelerOfferForm(forms.ModelForm):
    """Traveler's response to a buyer-first order (see PLAN-buyer-first-orders.md
    §3, §8). Becomes a 'leg' once the buyer selects it."""

    from_country = forms.ChoiceField(choices=COUNTRY_CHOICES)
    to_country = forms.ChoiceField(choices=COUNTRY_CHOICES)

    class Meta:
        model = TravelerOffer
        fields = [
            "ask_cost_per_kg", "avail_kg",
            "drop_off_address",
            "travel_date", "travel_time",
            "from_city", "from_country", "to_city", "to_country",
        ]
        widgets = {
            "ask_cost_per_kg": ThousandSeparatorNumberInput(),
            "avail_kg": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal", "placeholder": "e.g. 5.0"}
            ),
            "drop_off_address": forms.Textarea(attrs={"rows": 2, "placeholder": "Where the buyer should drop off the package"}),
            "travel_date": DATE_INPUT,
            "travel_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["travel_date"].input_formats = ["%d-%b-%Y", "%Y-%m-%d"]
        self.fields["drop_off_address"].required = True

    def clean_avail_kg(self):
        val = self.cleaned_data.get("avail_kg")
        if not val or val <= 0:
            raise forms.ValidationError("Enter your spare carrying capacity (kg).")
        return val

    def clean_ask_cost_per_kg(self):
        val = self.cleaned_data.get("ask_cost_per_kg")
        if not val or val <= 0:
            raise forms.ValidationError("Enter your rate per kg.")
        return val

    def clean_travel_date(self):
        val = self.cleaned_data.get("travel_date")
        if not val:
            raise forms.ValidationError("Travel date is required.")
        if val <= timezone.now().date():
            raise forms.ValidationError("Travel date must be in the future.")
        return val


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
        fields = ["name", "quantity", "unit", "photo"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Product Name"}),
            "unit": forms.TextInput(attrs={"placeholder": "pcs"}),
        }

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


class OrderItemForm(forms.ModelForm):
    """Buyer-first order item — unlike the traveler-first flow (where the
    traveler fills in cost after actually buying it), there's no separate
    purchase step here, so the buyer declares the price upfront for customs
    invoice purposes."""

    class Meta:
        model = RequestItem
        fields = ["name", "quantity", "unit", "photo", "estimated_unit_cost"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Product Name"}),
            "unit": forms.TextInput(attrs={"placeholder": "pcs"}),
            "estimated_unit_cost": ThousandSeparatorNumberInput(attrs={"placeholder": "Unit price"}),
        }

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and hasattr(photo, "size") and photo.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Image must be 2 MB or smaller.")
        if photo and hasattr(photo, "content_type"):
            if photo.content_type not in {"image/png", "image/jpeg", "image/jpg"}:
                raise forms.ValidationError("Only PNG, JPG or JPEG images are allowed.")
        return photo


OrderItemFormSet = inlineformset_factory(
    BuyRequest, RequestItem, form=OrderItemForm,
    extra=3, max_num=10, validate_max=True, can_delete=True,
)

# Traveler sets the estimated cost of each item when reviewing.
class ReviewItemForm(forms.ModelForm):
    class Meta:
        model = RequestItem
        fields = ["estimated_unit_cost"]
        widgets = {
            "estimated_unit_cost": ThousandSeparatorNumberInput(attrs={"class": "money-input num-right"}),
        }


ReviewItemFormSet = inlineformset_factory(
    BuyRequest, RequestItem, form=ReviewItemForm,
    extra=0, can_delete=False,
)

class PurchaseWeightForm(forms.ModelForm):
    """Traveler enters the actual (final-measured) shipment weight while recording
    the purchase, so the invoice's Actual shipment cost is calculated right then
    (rather than waiting for the package-arrived step)."""

    class Meta:
        model = BuyRequest
        fields = ["actual_weight_kg"]
        widgets = {
            "actual_weight_kg": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "inputmode": "decimal", "placeholder": "e.g. 3.60"}
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["actual_weight_kg"].required = False
        if self.instance and self.instance.pk and not self.instance.actual_weight_kg:
            self.initial["actual_weight_kg"] = self.instance.estimated_weight_kg


# Traveler records what was actually purchased (incl. actual quantity).
class PurchaseItemForm(forms.ModelForm):
    class Meta:
        model = RequestItem
        fields = ["actual_quantity", "actual_unit_cost", "purchase_photo", "purchase_note"]
        widgets = {
            "actual_quantity": forms.NumberInput(attrs={"class": "num-right", "min": "0"}),
            "actual_unit_cost": ThousandSeparatorNumberInput(attrs={"class": "money-input num-right"}),
            "purchase_note": forms.TextInput(
                attrs={"placeholder": "e.g. out of stock, bought 2 of 3, substituted"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            if not self.instance.actual_quantity:
                self.initial["actual_quantity"] = self.instance.quantity
            if not self.instance.actual_unit_cost:
                self.initial["actual_unit_cost"] = self.instance.estimated_unit_cost


PurchaseItemFormSet = inlineformset_factory(
    BuyRequest, RequestItem, form=PurchaseItemForm, extra=0, can_delete=False,
)


class CustomFareForm(forms.ModelForm):
    """Traveler at arrival: custom fare paid at destination (defaults to IDR)."""

    class Meta:
        model = BuyRequest
        fields = ["custom_fare_currency", "custom_fare_amount", "custom_fare_proof"]
        widgets = {
            "custom_fare_amount": ThousandSeparatorNumberInput(attrs={"class": "money-input num-right"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Duty is almost always paid in IDR at the destination — preselect it,
        # but leave the dropdown editable so the traveler can change it.
        if not self.instance.custom_fare_currency:
            self.initial["custom_fare_currency"] = Currency.IDR


class LegCustomFareForm(forms.ModelForm):
    """Traveler at a leg's arrival: customs duty paid at destination. Always
    in IDR (the destination currency); converted to the order currency via the
    kurs/fx table. Reimbursable by the buyer."""

    class Meta:
        model = TravelerOffer
        fields = ["custom_fare_currency", "custom_fare_amount", "custom_fare_proof"]
        widgets = {
            "custom_fare_currency": forms.HiddenInput(),
            "custom_fare_amount": ThousandSeparatorNumberInput(attrs={"class": "money-input num-right"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["custom_fare_currency"] = Currency.IDR

    def clean_custom_fare_currency(self):
        # Duty is always paid in IDR at the destination — force it.
        return Currency.IDR


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


class ReshipmentCostForm(forms.ModelForm):
    """Traveler sends reshipment cost to buyer. Bank details for the buyer's
    transfer come from the traveler's own profile (traveler_bank_details),
    not re-entered here."""

    class Meta:
        model = BuyRequest
        fields = ["reshipment_cost_amount", "reshipment_cost_proof"]
        labels = {
            "reshipment_cost_amount": "Shipment cost (IDR)",
            "reshipment_cost_proof": "Cost proof (optional)",
        }
        widgets = {
            "reshipment_cost_amount": ThousandSeparatorNumberInput(attrs={"class": "money-input num-right", "placeholder": "e.g. 100,000"}),
            "reshipment_cost_proof": forms.FileInput(),
        }

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("reshipment_cost_amount"):
            self.add_error("reshipment_cost_amount", "Required.")
        return cleaned


class AWBForm(forms.ModelForm):
    """Traveler uploads AWB number + document to mark package as shipped."""

    class Meta:
        model = BuyRequest
        fields = ["awb_number", "awb_document"]
        labels = {
            "awb_number": "AWB / Tracking number",
            "awb_document": "AWB document (PDF or image)",
        }
        widgets = {
            "awb_number": forms.TextInput(attrs={"placeholder": "e.g. JNE123456789"}),
            "awb_document": forms.FileInput(),
        }

    def clean_awb_number(self):
        val = self.cleaned_data.get("awb_number", "").strip()
        if not val:
            raise forms.ValidationError("AWB / tracking number is required.")
        return val
        return cleaned
