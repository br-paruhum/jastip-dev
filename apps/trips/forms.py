from datetime import timedelta
from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from .constants import COUNTRY_CHOICES, Currency, FulfillmentMethod
from .models import Order, Message, RequestItem, TravelerOffer, TravelPlan

# Text input enhanced by flatpickr (static/vendor/flatpickr). Displays and
# submits dd-Mmm-yyyy (e.g. 19-Jun-2026) on every browser — the native
# <input type="date"> follows the browser locale (e.g. mm/dd/yyyy on US
# English) and can't be forced.
DATE_INPUT = forms.DateInput(
    attrs={"class": "datepicker", "placeholder": "dd-Mmm-yyyy", "autocomplete": "off"},
    format="%d-%b-%Y",
)

# 24-hour time picker (flatpickr). Native <input type="time"> follows the browser
# locale (can show AM/PM), so we use a text input + flatpickr in 24h mode instead.
TIME_INPUT = forms.TimeInput(
    attrs={"class": "timepicker", "placeholder": "HH:MM", "autocomplete": "off"},
    format="%H:%M",
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
            "travel_time": TIME_INPUT,
            "shipment_cost_per_kg": ThousandSeparatorNumberInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["travel_date"].input_formats = ["%d-%b-%Y", "%Y-%m-%d"]

    def clean(self):
        cleaned = super().clean()
        # Carrier Only plans have no proxy-buying margin — force it to 0
        # regardless of what the (hidden) field submitted.
        if cleaned.get("carrier_only"):
            cleaned["margin_percent"] = Decimal("0")
        return cleaned


class BuyRequestForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ["estimated_weight_kg", "buyer_notes"]
        widgets = {
            "estimated_weight_kg": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal", "placeholder": "e.g. 2.5"}
            ),
            "buyer_notes": forms.Textarea(attrs={"rows": 4, "placeholder": "Notes for the carrier (optional)"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # On a brand-new request, don't pre-fill the model's default 0 — show the
        # placeholder instead so the buyer must enter a real weight (a stray 0
        # would otherwise sail through as the package weight).
        if self.instance.pk is None:
            self.initial["estimated_weight_kg"] = None


class OrderForm(forms.ModelForm):
    """Buyer-first 'place an order' form — no carrier yet (see
    PLAN-buyer-first-orders.md §3-4). Posts a Order with plan=None."""

    from_country = forms.ChoiceField(choices=COUNTRY_CHOICES)
    to_country = forms.ChoiceField(choices=COUNTRY_CHOICES)
    partial_allowed = forms.TypedChoiceField(
        choices=[("1", "Partial Allowed"), ("0", "Partial Not Allowed")],
        coerce=lambda v: v == "1",
        widget=forms.Select(),
        initial="0",
    )
    # Products (proxy buying, Flow 3) vs Cargo (carrying, Flow 4). Default Cargo
    # so existing buyer-first behaviour is unchanged until the buyer chooses.
    cargo_only = forms.TypedChoiceField(
        choices=[
            ("0", "Products — a Proxy Buyer purchases the items for me"),
            ("1", "Cargo — I already have the goods; a Carrier just brings them"),
        ],
        coerce=lambda v: v == "1",
        widget=forms.Select(),
        initial="0",
    )
    # Task 3: buyer's delivery preference, chosen at order creation.
    delivery_preference = forms.ChoiceField(
        choices=[
            (FulfillmentMethod.PICKUP, "Pick up at Carrier's location"),
            (FulfillmentMethod.RESHIP, "Reship package to Buyer's location"),
        ],
        widget=forms.Select(),
        initial=FulfillmentMethod.PICKUP,
        label="Delivery Preference",
    )

    class Meta:
        model = Order
        fields = [
            "from_city", "from_country", "to_city", "to_country",
            "to_address", "to_postal_code", "delivery_preference",
            "max_acceptable_date", "bid_weight_kg", "bid_cost_per_kg",
            "cargo_only", "partial_allowed", "buyer_notes",
        ]
        widgets = {
            "to_address": forms.Textarea(attrs={"rows": 2, "placeholder": "Destination delivery address"}),
            "max_acceptable_date": DATE_INPUT,
            "bid_weight_kg": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal",
                       "placeholder": "e.g. 2.5", "style": "text-align:right"}
            ),
            "bid_cost_per_kg": ThousandSeparatorNumberInput(attrs={"style": "text-align:right"}),
            "buyer_notes": forms.Textarea(attrs={"rows": 4, "placeholder": "Notes for the carrier (optional)"}),
        }

    def __init__(self, *args, proxy=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.proxy = proxy
        self.fields["max_acceptable_date"].input_formats = ["%d-%b-%Y", "%Y-%m-%d"]
        self.fields["max_acceptable_date"].label = "Order Deadline"
        # Flow-1 (proxy buying): the order is always a Products order, the origin
        # country is fixed by the chosen proxy, and the cargo/destination-logistics
        # fields don't apply (the proxy estimates weight/price; the address comes
        # from the buyer profile later). Drop them so the form only asks for what
        # the buyer fills in: route cities, products, and the order deadline.
        if proxy is not None:
            for name in (
                "cargo_only", "from_country", "from_city", "to_address", "to_postal_code",
                "buyer_notes", "bid_weight_kg", "bid_cost_per_kg", "partial_allowed",
            ):
                self.fields.pop(name, None)
            return
        # The bool model fields don't preselect their string-keyed choices on
        # edit (str(True) != "1"), so map them back explicitly.
        if self.instance and self.instance.pk:
            self.fields["cargo_only"].initial = "1" if self.instance.cargo_only else "0"
            self.fields["partial_allowed"].initial = "1" if self.instance.partial_allowed else "0"
        else:
            # New non-proxy order = Cargo (Flow-2). A Products order always goes
            # through a Proxy Buyer now, so default the type to Cargo so "Post an
            # Order" lands straight on the cargo form.
            self.fields["cargo_only"].initial = "1"
        # Start the bid fields blank instead of pre-filling the model's 0
        # default — otherwise typing "5" into the leading 0 produces "50".
        self.fields["bid_weight_kg"].initial = None
        self.fields["bid_cost_per_kg"].initial = None
        # Bid weight/price + partial only apply to a Cargo order; the Products
        # (proxy buying) flow has no buyer-set weight/price — the Proxy Buyer
        # estimates them. Required-ness is enforced in clean() per type.
        self.fields["bid_weight_kg"].required = False
        self.fields["bid_cost_per_kg"].required = False
        self.fields["partial_allowed"].required = False

    def clean(self):
        cleaned = super().clean()
        # Proxy (Flow-1) order: the cargo/bid fields were dropped from the form,
        # so leave them alone (the model defaults to 0/False) — re-adding them to
        # cleaned_data would break instance construction.
        if self.proxy is not None:
            return cleaned
        if cleaned.get("cargo_only"):
            # Cargo: bid weight + opening price are the carry-fee basis.
            if not cleaned.get("bid_weight_kg") or cleaned["bid_weight_kg"] <= 0:
                self.add_error("bid_weight_kg", "Enter the package weight (kg).")
            if not cleaned.get("bid_cost_per_kg") or cleaned["bid_cost_per_kg"] <= 0:
                self.add_error("bid_cost_per_kg", "Enter your opening price per kg.")
        else:
            # Products: no buyer-set weight/price/partial — the Proxy Buyer
            # quotes estimates in their offer. Neutralise the Cargo-only fields.
            cleaned["bid_weight_kg"] = Decimal("0")
            cleaned["bid_cost_per_kg"] = Decimal("0")
            cleaned["partial_allowed"] = False
        return cleaned

    def clean_max_acceptable_date(self):
        val = self.cleaned_data.get("max_acceptable_date")
        if not val:
            raise forms.ValidationError("Set a deadline for receiving offers.")
        earliest = timezone.now().date() + timedelta(days=7)
        if val < earliest:
            raise forms.ValidationError(
                f"Deadline must be at least 7 days from today "
                f"(on or after {earliest:%d-%b-%Y})."
            )
        return val


class TravelerOfferForm(forms.ModelForm):
    """Carrier's response to a buyer-first order (see PLAN-buyer-first-orders.md
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
            "travel_time": TIME_INPUT,
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


class TravelerCargoOfferForm(forms.ModelForm):
    """Carrier's carry offer on a 'Cargo Looking for Carrier' order (Flow-1/2):
    offer weight + shipment rate/kg + travel date/time. No drop-off address — the
    proxy buyer hands the package over to the carrier, so the buyer never drops
    anything off."""

    from_country = forms.ChoiceField(choices=COUNTRY_CHOICES)
    to_country = forms.ChoiceField(choices=COUNTRY_CHOICES)

    class Meta:
        model = TravelerOffer
        fields = [
            "ask_cost_per_kg", "avail_kg", "travel_date", "travel_time",
            "from_city", "from_country", "to_city", "to_country",
        ]
        widgets = {
            "ask_cost_per_kg": ThousandSeparatorNumberInput(attrs={"class": "money-input num-right"}),
            "avail_kg": forms.NumberInput(
                attrs={"step": "0.1", "min": "0.1", "inputmode": "decimal",
                       "placeholder": "e.g. 5.0", "class": "num-right"}
            ),
            "travel_date": DATE_INPUT,
            "travel_time": TIME_INPUT,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["travel_date"].input_formats = ["%d-%b-%Y", "%Y-%m-%d"]

    def clean_avail_kg(self):
        val = self.cleaned_data.get("avail_kg")
        if not val or val <= 0:
            raise forms.ValidationError("Enter your offered carrying weight (kg).")
        return val

    def clean_ask_cost_per_kg(self):
        val = self.cleaned_data.get("ask_cost_per_kg")
        if not val or val <= 0:
            raise forms.ValidationError("Enter your shipment cost per kg.")
        return val

    def clean_travel_date(self):
        val = self.cleaned_data.get("travel_date")
        if not val:
            raise forms.ValidationError("Travel date is required.")
        # The proxy buyer needs lead time to purchase the goods after the buyer
        # accepts this offer, so the travel date must be more than 3 days out.
        if val <= timezone.now().date() + timedelta(days=3):
            raise forms.ValidationError(
                "Travel date must be more than 3 days from today, to provide "
                "proxy buyer enough time buying the goods."
            )
        return val


class ReviewForm(forms.ModelForm):
    """Carrier-side review field: estimated weight of this package (kg).

    Optional here (so a draft can be saved without it); the view requires a
    positive value only when the carrier clicks Accept.
    """

    class Meta:
        model = Order
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


class ProxyEstimateForm(forms.ModelForm):
    """Flow-1 (proxy buying) 'Send Estimate' — the proxy quotes the total weight
    and their margin %; the per-item unit costs come from ReviewItemFormSet."""

    class Meta:
        model = Order
        fields = ["estimated_weight_kg", "proxy_margin_percent"]
        widgets = {
            "estimated_weight_kg": forms.NumberInput(
                attrs={"step": "0.1", "min": "0.1", "inputmode": "decimal",
                       "placeholder": "e.g. 2.5", "class": "num-right"}
            ),
            "proxy_margin_percent": forms.NumberInput(
                attrs={"step": "0.01", "min": "0", "inputmode": "decimal",
                       "placeholder": "e.g. 20", "class": "num-right"}
            ),
        }

    def clean_estimated_weight_kg(self):
        val = self.cleaned_data.get("estimated_weight_kg")
        if not val or val <= 0:
            raise forms.ValidationError("Enter the estimated total weight (kg).")
        return val

    def clean_proxy_margin_percent(self):
        val = self.cleaned_data.get("proxy_margin_percent")
        if val is None or val < 0:
            raise forms.ValidationError("Enter your margin (%).")
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
    Order, RequestItem, form=RequestItemForm,
    extra=3, max_num=10, validate_max=True, can_delete=True,
)


class OrderItemForm(forms.ModelForm):
    """Buyer-first order item — unlike the carrier-first flow (where the
    carrier fills in cost after actually buying it), there's no separate
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Blank the model-default 0 on new item rows so the placeholder shows and
        # the buyer can't accidentally declare a 0 value for customs.
        if self.instance.pk is None:
            self.initial["estimated_unit_cost"] = None

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and hasattr(photo, "size") and photo.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Image must be 2 MB or smaller.")
        if photo and hasattr(photo, "content_type"):
            if photo.content_type not in {"image/png", "image/jpeg", "image/jpg"}:
                raise forms.ValidationError("Only PNG, JPG or JPEG images are allowed.")
        return photo


OrderItemFormSet = inlineformset_factory(
    Order, RequestItem, form=OrderItemForm,
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
    Order, RequestItem, form=ReviewItemForm,
    extra=0, can_delete=False,
)

class PurchaseWeightForm(forms.ModelForm):
    """Carrier enters the actual (final-measured) shipment weight while recording
    the purchase, so the invoice's Actual shipment cost is calculated right then
    (rather than waiting for the package-arrived step)."""

    class Meta:
        model = Order
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
                attrs={"placeholder": "Note: e.g.: short availability, out-of-stock or substituted as agreed",
                       "style": "width:560px;max-width:100%;font-size:1.05rem"}
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
    Order, RequestItem, form=PurchaseItemForm, extra=0, can_delete=False,
)


class CustomFareForm(forms.ModelForm):
    """Carrier at arrival: customs duty ESTIMATE the buyer pays upfront, plus the
    estimate proof and (later, after the buyer pays) the actual receipt."""

    class Meta:
        model = Order
        fields = [
            "custom_fare_currency", "custom_fare_amount",
            "custom_fare_estimate_proof", "custom_fare_proof",
        ]
        widgets = {
            "custom_fare_amount": ThousandSeparatorNumberInput(attrs={"class": "money-input num-right"}),
            # Currency is fixed to the destination country's currency (shown muted
            # in the template); a plain file input avoids the clear/change widgets.
            "custom_fare_currency": forms.HiddenInput(),
            "custom_fare_estimate_proof": forms.FileInput(attrs={"accept": "image/png,image/jpeg"}),
            "custom_fare_proof": forms.FileInput(attrs={"accept": "image/png,image/jpeg"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Customs duty is always paid in the destination country's currency.
        self.initial["custom_fare_currency"] = self.instance.destination_currency
        # No duty is the common case — let the amount be left blank (treated as
        # zero) so the carrier can send the estimate without typing a 0. Both
        # proofs are optional (the actual receipt comes after the buyer pays).
        self.fields["custom_fare_amount"].required = False
        self.fields["custom_fare_estimate_proof"].required = False
        self.fields["custom_fare_proof"].required = False

    def clean_custom_fare_amount(self):
        return self.cleaned_data.get("custom_fare_amount") or Decimal("0")


class LegCustomFareForm(forms.ModelForm):
    """Carrier at a leg's arrival: customs duty paid at destination, in that
    country's currency (e.g. SGD for Singapore); converted to the order currency
    via the kurs/fx table. Reimbursable by the buyer."""

    class Meta:
        model = TravelerOffer
        fields = ["custom_fare_currency", "custom_fare_amount", "custom_fare_proof"]
        widgets = {
            "custom_fare_currency": forms.HiddenInput(),
            "custom_fare_amount": ThousandSeparatorNumberInput(attrs={"class": "money-input num-right"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Customs duty is paid in the leg's destination-country currency.
        self.initial["custom_fare_currency"] = self.instance.destination_currency

    def clean_custom_fare_currency(self):
        # Fixed to the destination currency — don't trust the hidden input.
        return self.instance.destination_currency


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
        model = Order
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
    """Carrier sends reshipment cost to buyer. Bank details for the buyer's
    transfer come from the carrier's own profile (traveler_bank_details),
    not re-entered here."""

    class Meta:
        model = Order
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
    """Carrier uploads AWB number + document to mark package as shipped."""

    class Meta:
        model = Order
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
