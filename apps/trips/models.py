from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .constants import (
    ACTIVE_TX_STATUSES,
    DEFAULT_PAYMENT_TERM,
    OPEN_PLAN_STATUSES,
    STATUS_TONE,
    Currency,
    Status,
)

TWO_PLACES = Decimal("0.01")
USER = settings.AUTH_USER_MODEL


class TravelPlan(models.Model):
    """A traveler's offer: a trip with spare luggage capacity."""

    traveler = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="travel_plans")
    reference = models.CharField(max_length=12, unique=True, editable=False, blank=True)

    travel_date = models.DateField()
    from_city = models.CharField(max_length=80)
    from_country = models.CharField(max_length=80)
    to_city = models.CharField(max_length=80)
    to_country = models.CharField(max_length=80)

    available_weight_kg = models.DecimalField(max_digits=6, decimal_places=2)
    shipment_currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.USD)
    shipment_cost_per_kg = models.DecimalField(max_digits=12, decimal_places=2)
    margin_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"),
        help_text="Applied on top of the ordered items cost.",
    )
    payment_term = models.TextField(default=DEFAULT_PAYMENT_TERM)

    status = models.CharField(max_length=24, choices=Status.choices, default=Status.NEW)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["travel_date", "-created_at"]
        indexes = [models.Index(fields=["status", "travel_date"])]

    def __str__(self):
        return f"{self.reference} · {self.from_city}→{self.to_city} · {self.travel_date:%d-%b-%Y}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self._generate_reference()
        super().save(*args, **kwargs)

    def _generate_reference(self) -> str:
        import secrets
        prefix = slugify(self.to_city)[:3].upper() or "JST"
        return f"{prefix}-{secrets.token_hex(3).upper()}"

    def get_absolute_url(self):
        return reverse("trips:plan_detail", args=[self.pk])

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_PLAN_STATUSES

    @property
    def is_closed(self) -> bool:
        return self.status == Status.CLOSED

    @property
    def status_tone(self) -> str:
        return STATUS_TONE.get(self.status, "muted")

    @property
    def route(self) -> str:
        return f"{self.from_city}, {self.from_country} → {self.to_city}, {self.to_country}"

    @property
    def active_request(self):
        """The buy request currently driving this plan's lifecycle, if any."""
        return self.buy_requests.exclude(status=Status.REJECTED).order_by("-created_at").first()


class BuyRequest(models.Model):
    """A buyer 'blocks' a travel plan and lists items to purchase.

    This carries the transaction lifecycle from 'Request Received' onward.
    """

    plan = models.ForeignKey(TravelPlan, on_delete=models.CASCADE, related_name="buy_requests")
    buyer = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="buy_requests")
    reference = models.CharField(max_length=14, unique=True, editable=False, blank=True)

    status = models.CharField(max_length=24, choices=Status.choices, default=Status.REQUEST_RECEIVED)
    buyer_notes = models.TextField(blank=True)

    # Custom fare paid by traveler at destination (reimbursable), filled on arrival.
    custom_fare_currency = models.CharField(
        max_length=3, choices=Currency.choices, blank=True, default=""
    )
    custom_fare_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0")
    )
    custom_fare_proof = models.ImageField(upload_to="custom_fare/", blank=True, null=True)

    rejection_reason = models.TextField(blank=True)
    traveler_cleared = models.BooleanField(default=False)
    buyer_cleared = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.reference:
            import secrets
            self.reference = f"REQ-{secrets.token_hex(4).upper()}"
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("trips:request_detail", args=[self.pk])

    @property
    def status_tone(self) -> str:
        return STATUS_TONE.get(self.status, "muted")

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_TX_STATUSES

    # --- Money (no FX; the plan's shipment currency is the settlement currency) ---
    @property
    def currency(self) -> str:
        return self.plan.shipment_currency

    def _q(self, value: Decimal) -> Decimal:
        return Decimal(value).quantize(TWO_PLACES)

    @property
    def items_estimated_total(self) -> Decimal:
        return self._q(sum((i.estimated_line_total for i in self.items.all()), Decimal("0")))

    @property
    def items_actual_total(self) -> Decimal:
        return self._q(sum((i.actual_line_total for i in self.items.all()), Decimal("0")))

    @property
    def shipment_cost(self) -> Decimal:
        return self._q(self.plan.available_weight_kg * self.plan.shipment_cost_per_kg)

    @property
    def margin_amount(self) -> Decimal:
        base = self.items_actual_total or self.items_estimated_total
        return self._q(base * self.plan.margin_percent / Decimal("100"))

    @property
    def deposit_due(self) -> Decimal:
        """50% of items + 100% of shipment (the up-front transfer)."""
        items = self.items_estimated_total
        return self._q(items * Decimal("0.5") + self.shipment_cost)

    @property
    def invoice_total(self) -> Decimal:
        """Full cost: actual items + margin + shipment + custom fare."""
        return self._q(
            self.items_actual_total
            + self.margin_amount
            + self.shipment_cost
            + self.custom_fare_amount
        )

    @property
    def amount_paid(self) -> Decimal:
        verified = self.transaction.payments.filter(
            direction=Payment.Direction.INBOUND, status=Payment.PaymentStatus.VERIFIED
        ) if hasattr(self, "transaction") else []
        return self._q(sum((p.amount for p in verified), Decimal("0")))

    @property
    def unpaid_amount(self) -> Decimal:
        return self._q(self.invoice_total - self.amount_paid)


class RequestItem(models.Model):
    """One requested item; the traveler fills in cost + actual-purchase fields."""

    request = models.ForeignKey(BuyRequest, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveSmallIntegerField(default=1)

    name = models.CharField(max_length=200)
    quantity = models.PositiveSmallIntegerField(default=1)
    photo = models.ImageField(upload_to="items/requested/", blank=True, null=True)

    # Set by the traveler when reviewing the request ("update the cost fields").
    estimated_unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    # Set by the traveler after actually purchasing.
    actual_unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    purchase_photo = models.ImageField(upload_to="items/purchased/", blank=True, null=True)
    purchased_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.name} ×{self.quantity}"

    @property
    def estimated_line_total(self) -> Decimal:
        return (self.estimated_unit_cost or Decimal("0")) * self.quantity

    @property
    def actual_line_total(self) -> Decimal:
        return (self.actual_unit_cost or Decimal("0")) * self.quantity

    @property
    def is_purchased(self) -> bool:
        return self.purchased_at is not None


class Transaction(models.Model):
    """Settlement record for a buy request. One transaction per request.

    Gateway-ready: payments below carry a `method` and provider fields so a
    real gateway (Midtrans/Stripe) can be plugged in without schema changes.
    """

    request = models.OneToOneField(BuyRequest, on_delete=models.CASCADE, related_name="transaction")
    commission_percent = models.DecimalField(
        max_digits=5, decimal_places=2,
        default=Decimal(str(settings.PLATFORM_COMMISSION_PERCENT)),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"TX for {self.request.reference}"

    @property
    def currency(self) -> str:
        return self.request.currency

    @property
    def commission_amount(self) -> Decimal:
        """Platform fee: 2.5% of the full invoice, deducted once at closing."""
        return (self.request.invoice_total * self.commission_percent / Decimal("100")).quantize(TWO_PLACES)

    @property
    def payout_to_traveler(self) -> Decimal:
        """Paid to the traveler when the transaction CLOSES (both parties
        cleared): the full invoice (items + margin + shipment + custom fare)
        minus the 2.5% platform fee. The deposit is only held by admin while
        the traveler purchases — there is no payout before closing.
        """
        return (self.request.invoice_total - self.commission_amount).quantize(TWO_PLACES)


class Payment(models.Model):
    """A single money movement. Manual now, gateway-ready by design."""

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound (to admin)"
        OUTBOUND = "outbound", "Outbound (from admin)"

    class Kind(models.TextChoices):
        DEPOSIT = "deposit", "Buyer deposit"
        BALANCE = "balance", "Buyer balance (unpaid amount)"
        PAYOUT = "payout", "Payout to traveler"
        REFUND = "refund", "Refund"

    class Method(models.TextChoices):
        MANUAL = "manual", "Manual transfer"
        GATEWAY = "gateway", "Payment gateway"

    class PaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified / Paid"
        FAILED = "failed", "Failed"

    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="payments")
    direction = models.CharField(max_length=10, choices=Direction.choices)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.MANUAL)

    currency = models.CharField(max_length=3, choices=Currency.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    proof = models.ImageField(upload_to="payments/proof/", blank=True, null=True)

    # Gateway plumbing (unused in manual mode).
    provider = models.CharField(max_length=40, blank=True)
    provider_ref = models.CharField(max_length=120, blank=True)

    verified_by = models.ForeignKey(
        USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="verified_payments"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount} {self.currency} ({self.get_status_display()})"

    def mark_verified(self, by_user=None):
        self.status = self.PaymentStatus.VERIFIED
        self.verified_by = by_user
        self.verified_at = timezone.now()
        self.save(update_fields=["status", "verified_by", "verified_at"])
