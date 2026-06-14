from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .constants import (
    ACTIVE_TX_STATUSES,
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
    shipment_currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.IDR)
    shipment_cost_per_kg = models.DecimalField(max_digits=12, decimal_places=2)
    margin_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"),
        help_text="Applied on top of the ordered items cost.",
    )
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

    @property
    def utilized_weight_kg(self) -> Decimal:
        """Spare weight already taken up by current orders.

        Counts every non-rejected buy request, using its actual weight once the
        buyer has confirmed it (set at pickup), otherwise the traveler's
        estimate. So the figure starts showing as soon as an estimate is entered
        and is automatically corrected to the actual weight later — giving the
        traveler no benefit from over-estimating.

        Iterates the prefetched ``buy_requests`` cache (filters in Python) to
        avoid an extra query per plan in list views.
        """
        total = Decimal("0")
        for req in self.buy_requests.all():
            if req.status == Status.REJECTED:
                continue
            total += req.actual_weight_kg if req.has_actual_weight else req.estimated_weight_kg
        return total.quantize(TWO_PLACES)

    @property
    def remaining_weight_kg(self) -> Decimal:
        """Spare weight still available to fill (never negative for display)."""
        remaining = self.available_weight_kg - self.utilized_weight_kg
        return remaining if remaining > 0 else Decimal("0").quantize(TWO_PLACES)


class BuyRequest(models.Model):
    """A buyer 'orders' a travel plan and lists items to purchase.

    This carries the transaction lifecycle from 'Ordered' onward.
    """

    plan = models.ForeignKey(TravelPlan, on_delete=models.CASCADE, related_name="buy_requests")
    buyer = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="buy_requests")
    reference = models.CharField(max_length=14, unique=True, editable=False, blank=True)

    status = models.CharField(max_length=24, choices=Status.choices, default=Status.REQUEST_RECEIVED)
    buyer_notes = models.TextField(blank=True)

    # Estimated shipping weight of THIS package, set by the traveler at review.
    # Shipment cost is charged on this weight, not the plan's full capacity.
    estimated_weight_kg = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    # Actual weight, set by the buyer when checking the package at pickup. Once
    # set it supersedes the estimate for the final invoice + balance.
    actual_weight_kg = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))

    # Custom fare paid by traveler at destination (reimbursable), filled on arrival.
    custom_fare_currency = models.CharField(
        max_length=3, choices=Currency.choices, blank=True, default=""
    )
    custom_fare_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0")
    )
    custom_fare_proof = models.ImageField(upload_to="custom_fare/", blank=True, null=True)

    # Buyer's bank details for refunding an overpaid amount (if any).
    refund_bank_name = models.CharField(max_length=120, blank=True)
    refund_account_no = models.CharField(max_length=60, blank=True)
    refund_account_name = models.CharField(max_length=120, blank=True)
    refund_processed = models.BooleanField(default=False)

    rejection_reason = models.TextField(blank=True)
    traveler_cleared = models.BooleanField(default=False)
    buyer_cleared = models.BooleanField(default=False)
    cleared_at = models.DateTimeField(null=True, blank=True, help_text="When the buyer marked the package Clear.")

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
        # Transaction detail is embedded as an in-page panel on the profile page.
        return reverse("accounts:profile") + f"?order={self.pk}#order-detail"

    @property
    def status_tone(self) -> str:
        return STATUS_TONE.get(self.status, "muted")

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_TX_STATUSES

    @property
    def detail_status_label(self) -> str:
        """Status label shown on the request detail page. Mirrors the global
        status display (e.g. 'Ordered' for REQUEST_RECEIVED)."""
        return self.get_status_display()

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
    def estimated_shipment_cost(self) -> Decimal:
        """Shipment on the traveler's estimated weight (drives the deposit)."""
        return self._q(self.estimated_weight_kg * self.plan.shipment_cost_per_kg)

    @property
    def shipment_cost(self) -> Decimal:
        """Actual shipment = traveler's final measured weight × rate."""
        return self._q(self.actual_weight_kg * self.plan.shipment_cost_per_kg)

    @property
    def has_actual_weight(self) -> bool:
        return bool(self.actual_weight_kg and self.actual_weight_kg > 0)

    @property
    def refund_details_provided(self) -> bool:
        return bool(self.refund_account_no and self.refund_account_name)

    # --- Quantities (Est = original request, Act = traveler's actuals) ---
    @property
    def est_qty_total(self) -> int:
        return sum(i.quantity for i in self.items.all())

    @property
    def act_qty_total(self) -> int:
        return sum(i.actual_quantity for i in self.items.all())

    # --- Margin (Est on estimated items, Act on actual items) ---
    @property
    def estimated_margin(self) -> Decimal:
        return self._q(self.items_estimated_total * self.plan.margin_percent / Decimal("100"))

    @property
    def actual_margin(self) -> Decimal:
        return self._q(self.items_actual_total * self.plan.margin_percent / Decimal("100"))

    # Backwards-compatible alias (actual margin drives the live invoice/payout).
    @property
    def margin_amount(self) -> Decimal:
        return self.actual_margin

    # --- Shipment + custom (Est vs Act) ---
    @property
    def actual_shipment_cost(self) -> Decimal:
        return self.shipment_cost  # uses actual weight once set, else estimate

    @property
    def estimated_custom(self) -> Decimal:
        return Decimal("0.00")

    @property
    def actual_custom(self) -> Decimal:
        return self._q(self.custom_fare_amount)

    # --- Totals ---
    @property
    def estimated_invoice_total(self) -> Decimal:
        """Est column total: estimated items + est margin + est shipment."""
        return self._q(
            self.items_estimated_total + self.estimated_margin
            + self.estimated_shipment_cost + self.estimated_custom
        )

    @property
    def invoice_total(self) -> Decimal:
        """Actual column total: actual items + actual margin + actual shipment + custom."""
        return self._q(
            self.items_actual_total + self.actual_margin
            + self.actual_shipment_cost + self.actual_custom
        )

    @property
    def deposit_due(self) -> Decimal:
        """Deposit = ((Estimated items + estimated margin) × 50%) + estimated
        shipment. Fixed on estimates at acceptance."""
        return self._q(
            (self.items_estimated_total + self.estimated_margin) * Decimal("0.5")
            + self.estimated_shipment_cost
        )

    @property
    def invoice_unpaid_overpaid(self) -> Decimal:
        """Invoice statement line: actual Total Invoice − Deposit Paid.
        Positive = buyer still owes, negative = overpaid.

        Before the traveler records the actual purchase the Actual column is
        empty (Total Invoice = 0). There is nothing to reconcile against the
        deposit yet, so this reads 0 instead of showing the whole deposit as
        'overpaid' (which confused buyers right after the deposit was verified)."""
        total = self.invoice_total
        if total <= 0:
            return Decimal("0.00")
        return self._q(total - self.deposit_paid_amount)

    @property
    def final_settlement(self) -> Decimal:
        """The balance (payment)/refund actually made after the deposit.

        Shown as a credit, like Deposit Paid: a balance payment reduces what is
        due (negative), a refund returned to the buyer increases it (positive).
        Stays zero until the buyer actually settles the post-purchase balance —
        previously this assumed the full balance was already paid, so the
        statement showed Total Due 0 while the payment was still outstanding."""
        return self._q(self.total_refunded - self.balance_paid_amount)

    @property
    def total_due(self) -> Decimal:
        """Outstanding after the deposit and any final settlement actually made.
        Positive = buyer still owes; negative = refund owed to the buyer."""
        return self._q(self.invoice_unpaid_overpaid + self.final_settlement)

    def _paid_of_kind(self, kind=None) -> Decimal:
        if not hasattr(self, "transaction"):
            return Decimal("0.00")
        qs = self.transaction.payments.filter(
            direction=Payment.Direction.INBOUND, status=Payment.PaymentStatus.VERIFIED
        )
        if kind is not None:
            qs = qs.filter(kind=kind)
        return self._q(sum((p.amount for p in qs), Decimal("0")))

    @property
    def gross_received(self) -> Decimal:
        """All verified inbound payments from the buyer."""
        return self._paid_of_kind()

    @property
    def total_refunded(self) -> Decimal:
        """Verified refunds already paid back to the buyer (outbound)."""
        if not hasattr(self, "transaction"):
            return Decimal("0.00")
        qs = self.transaction.payments.filter(
            direction=Payment.Direction.OUTBOUND,
            status=Payment.PaymentStatus.VERIFIED,
            kind=Payment.Kind.REFUND,
        )
        return self._q(sum((p.amount for p in qs), Decimal("0")))

    @property
    def amount_paid(self) -> Decimal:
        """Net the buyer has paid us: inbound received minus refunds returned."""
        return self._q(self.gross_received - self.total_refunded)

    @property
    def deposit_paid_amount(self) -> Decimal:
        return self._paid_of_kind(Payment.Kind.DEPOSIT)

    @property
    def balance_paid_amount(self) -> Decimal:
        """Verified non-deposit (balance / top-up) payments."""
        return self._paid_of_kind(Payment.Kind.BALANCE)

    @property
    def unpaid_amount(self) -> Decimal:
        return self._q(self.invoice_total - self.amount_paid)

    @property
    def extra_due(self) -> Decimal:
        """Amount the buyer still owes (e.g. actual weight > estimate)."""
        u = self.unpaid_amount
        return u if u > 0 else Decimal("0.00")

    @property
    def refund_due(self) -> Decimal:
        """Amount to refund the buyer (e.g. actual weight < estimate)."""
        u = self.unpaid_amount
        return -u if u < 0 else Decimal("0.00")


class Refund(BuyRequest):
    """Proxy of BuyRequest powering the admin 'Refunds' section: the same orders,
    but presented as a dedicated workspace for processing overpaid refunds so the
    refund action no longer lives under the confusing 'Buy Requests' label."""

    class Meta:
        proxy = True
        verbose_name = "Refund"
        verbose_name_plural = "Refunds"


class RequestItem(models.Model):
    """One requested item; the traveler fills in cost + actual-purchase fields."""

    request = models.ForeignKey(BuyRequest, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveSmallIntegerField(default=1)

    name = models.CharField(max_length=200)
    quantity = models.PositiveSmallIntegerField(default=1)
    unit = models.CharField(max_length=20, default="pcs", blank=True, help_text="Unit of measure, e.g. pcs, box, kg.")
    photo = models.ImageField(upload_to="items/requested/", blank=True, null=True)

    # Set by the traveler when reviewing the request ("update the cost fields").
    estimated_unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    # Set by the traveler after actually purchasing.
    actual_quantity = models.PositiveSmallIntegerField(default=0, help_text="Quantity actually purchased.")
    actual_unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    purchase_photo = models.ImageField(upload_to="items/purchased/", blank=True, null=True)
    purchase_note = models.CharField(
        max_length=255, blank=True,
        help_text="Note if the item is unavailable, short, or substituted.",
    )
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
        return (self.actual_unit_cost or Decimal("0")) * self.actual_quantity

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
        return f"{self.get_kind_display()} {self.amount:,.2f} {self.currency} ({self.get_status_display()})"

    def mark_verified(self, by_user=None):
        self.status = self.PaymentStatus.VERIFIED
        self.verified_by = by_user
        self.verified_at = timezone.now()
        self.save(update_fields=["status", "verified_by", "verified_at"])


class ExchangeRate(models.Model):
    """BCA TT Counter exchange rate, fetched daily by `fetch_kurs`."""

    code = models.CharField(max_length=6, primary_key=True)
    name = models.CharField(max_length=40)
    sell_rate = models.DecimalField(max_digits=14, decimal_places=4)
    buy_rate = models.DecimalField(max_digits=14, decimal_places=4)
    is_active = models.BooleanField(default=True)
    sequence = models.PositiveSmallIntegerField(default=0, help_text="Lower = shown first.")
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sequence", "code"]

    def __str__(self):
        return f"{self.code} — sell {self.sell_rate:,.2f}"


class Message(models.Model):
    """A chat message between the buyer and traveler on a request (admin can
    read all threads for oversight)."""

    request = models.ForeignKey(BuyRequest, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="sent_messages")
    body = models.TextField()
    photo = models.ImageField(upload_to="chat/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["request", "created_at"])]

    def __str__(self):
        return f"msg #{self.pk} on {self.request.reference}"

    def role_for(self, request_obj) -> str:
        if self.sender_id == request_obj.buyer_id:
            return "Buyer"
        if self.sender_id == request_obj.plan.traveler_id:
            return "Traveler"
        return "Admin"
