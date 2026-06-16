from datetime import datetime, time
from decimal import Decimal
from types import SimpleNamespace

from django.conf import settings
from django.db import models

from .storage import webp_storage
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .constants import (
    ACTIVE_TX_STATUSES,
    OPEN_ORDER_STATUSES,
    OPEN_PLAN_STATUSES,
    STATUS_TONE,
    Currency,
    FulfillmentMethod,
    LegStatus,
    OfferStatus,
    Status,
)

TWO_PLACES = Decimal("0.01")
USER = settings.AUTH_USER_MODEL


class TravelPlan(models.Model):
    """A traveler's offer: a trip with spare luggage capacity."""

    traveler = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="travel_plans")
    reference = models.CharField(max_length=12, unique=True, editable=False, blank=True)

    travel_date = models.DateField()
    travel_time = models.TimeField(null=True, blank=True)
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
    carrier_only = models.BooleanField(
        default=False,
        help_text="Traveler only carries luggage space — not willing to act as a proxy buyer.",
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
    def travel_date_passed(self) -> bool:
        return timezone.now().date() >= self.travel_date

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_PLAN_STATUSES

    @property
    def active_requests(self):
        """All buy requests still in an active lifecycle (not rejected, cancelled, or closed).
        Iterates the prefetched buy_requests cache — no extra query per plan."""
        excluded = {Status.REJECTED, Status.CANCELLED, Status.CLOSED}
        return [r for r in self.buy_requests.all() if r.status not in excluded]

    @property
    def active_requests_with_capacity(self):
        """Active requests in creation order, each annotated with first-come-first-served
        available/remaining capacity. Returns a list of SimpleNamespace(req, available, remaining).
        Uses the prefetched buy_requests cache — no extra queries."""
        excluded = {Status.REJECTED, Status.CANCELLED, Status.CLOSED}
        ordered = sorted(
            [r for r in self.buy_requests.all() if r.status not in excluded],
            key=lambda r: r.created_at,
        )
        running = self.available_weight_kg
        result = []
        for req in ordered:
            avail = running.quantize(TWO_PLACES)
            weight = req.actual_weight_kg if req.has_actual_weight else req.estimated_weight_kg
            remaining = max(running - weight, Decimal("0")).quantize(TWO_PLACES)
            result.append(SimpleNamespace(req=req, available=avail, remaining=remaining))
            running = remaining
        return result

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
        excluded = {Status.REJECTED, Status.CANCELLED}
        total = Decimal("0")
        for req in self.buy_requests.all():
            if req.status in excluded:
                continue
            total += req.actual_weight_kg if req.has_actual_weight else req.estimated_weight_kg
        return total.quantize(TWO_PLACES)

    @property
    def remaining_weight_kg(self) -> Decimal:
        """Spare weight still available to fill (never negative for display)."""
        remaining = self.available_weight_kg - self.utilized_weight_kg
        return remaining if remaining > 0 else Decimal("0").quantize(TWO_PLACES)


class BuyRequest(models.Model):
    """A buyer 'orders' a travel plan and lists items to purchase — OR, when
    ``plan`` is null, a buyer-first order posted with no traveler yet (see
    PLAN-buyer-first-orders.md). Matching travelers respond via TravelerOffer;
    once selected, each offer becomes an independent "leg" with its own
    deposit and lifecycle (partial fulfillment can split one order across
    several travelers).

    This carries the transaction lifecycle from 'Ordered' onward.
    """

    plan = models.ForeignKey(
        TravelPlan, on_delete=models.CASCADE, related_name="buy_requests", null=True, blank=True
    )
    buyer = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="buy_requests")
    reference = models.CharField(max_length=14, unique=True, editable=False, blank=True)

    status = models.CharField(max_length=24, choices=Status.choices, default=Status.REQUEST_RECEIVED)
    buyer_notes = models.TextField(blank=True)

    # --- Buyer-first fields (used only when plan is null) ---
    from_city = models.CharField(max_length=80, blank=True)
    from_country = models.CharField(max_length=80, blank=True)
    to_city = models.CharField(max_length=80, blank=True)
    to_country = models.CharField(max_length=80, blank=True)
    to_address = models.TextField(blank=True)
    to_postal_code = models.CharField(max_length=20, blank=True)
    settlement_currency = models.CharField(max_length=3, choices=Currency.choices, blank=True, default="")
    max_acceptable_date = models.DateField(
        null=True, blank=True,
        help_text="Deadline for receiving TravelerOffers, and the drop-off grace-period deadline.",
    )
    bid_weight_kg = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    bid_cost_per_kg = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    partial_allowed = models.BooleanField(
        default=False, help_text="Allow this order to be split across multiple travelers."
    )

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
    custom_fare_proof = models.ImageField(upload_to="custom_fare/", blank=True, null=True, storage=webp_storage)

    # Buyer's bank details for refunding an overpaid amount (if any).
    refund_bank_name = models.CharField(max_length=120, blank=True)
    refund_account_no = models.CharField(max_length=60, blank=True)
    refund_account_name = models.CharField(max_length=120, blank=True)
    refund_processed = models.BooleanField(default=False)

    # Reshipment — step 1: buyer's delivery address.
    reshipment_address = models.TextField(blank=True)
    # Reshipment — step 2: traveler's cost + bank details.
    reshipment_cost_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    reshipment_cost_proof = models.ImageField(upload_to="reshipment_costs/", blank=True, null=True, storage=webp_storage)
    reshipment_bank_name = models.CharField(max_length=120, blank=True)
    reshipment_bank_account_no = models.CharField(max_length=60, blank=True)
    reshipment_bank_account_name = models.CharField(max_length=120, blank=True)
    # Reshipment — step 3: buyer uploads proof of paying the reshipment cost.
    reshipment_proof = models.ImageField(upload_to="reshipment_proofs/", blank=True, null=True, storage=webp_storage)
    # Reshipment — step 4: traveler uploads AWB + waybill.
    awb_number = models.CharField(max_length=80, blank=True)
    awb_document = models.FileField(upload_to="awb/", blank=True, null=True)

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
    def is_accepting_offers(self) -> bool:
        return self.status in OPEN_ORDER_STATUSES

    # --- Buyer-first resolvers (fall back to plan when present, else use the
    # order's own fields — see PLAN-buyer-first-orders.md §4) ---
    @property
    def is_buyer_first(self) -> bool:
        return self.plan_id is None

    @property
    def route(self) -> str:
        if self.plan_id:
            return self.plan.route
        return f"{self.from_city}, {self.from_country} → {self.to_city}, {self.to_country}"

    @property
    def traveler_user(self):
        """The traveler driving this order. Plan-first: the plan's traveler.
        Buyer-first: only meaningful once exactly one leg is confirmed — a
        partially-fulfilled order has several travelers, so this returns None
        and callers should iterate ``confirmed_legs`` instead."""
        if self.plan_id:
            return self.plan.traveler
        legs = self.confirmed_legs
        return legs[0].traveler if len(legs) == 1 else None

    @property
    def effective_cost_per_kg(self) -> Decimal:
        """Shipment rate driving cost calculations: the plan's rate, the
        single confirmed leg's accepted ask, or — before any leg is
        confirmed — the buyer's opening bid (for list-page display only)."""
        if self.plan_id:
            return self.plan.shipment_cost_per_kg
        legs = self.confirmed_legs
        if len(legs) == 1:
            return legs[0].ask_cost_per_kg
        return self.bid_cost_per_kg

    @property
    def confirmed_legs(self) -> list:
        """Selected TravelerOffers ("legs") for this order, oldest first."""
        return [o for o in self.traveler_offers.all() if o.offer_status == OfferStatus.SELECTED]

    @property
    def pending_offers(self) -> list:
        return [o for o in self.traveler_offers.all() if o.offer_status == OfferStatus.PENDING]

    @property
    def total_allocated_weight_kg(self) -> Decimal:
        return self._q(sum((leg.allocated_weight_kg or Decimal("0")) for leg in self.confirmed_legs))

    @property
    def remaining_bid_weight_kg(self) -> Decimal:
        return self._q(self.bid_weight_kg - self.total_allocated_weight_kg)

    @property
    def is_fully_matched(self) -> bool:
        return bool(self.confirmed_legs) and self.total_allocated_weight_kg >= self.bid_weight_kg

    # Per-leg progress order used to find the "least-progressed active leg"
    # for the order-level status rollup below.
    _LEG_PROGRESS_ORDER = [
        LegStatus.PACKAGE_DROPPED_OFF, LegStatus.WEIGHT_VERIFIED, LegStatus.PACKAGE_RECEIVED,
        LegStatus.PACKAGE_ARRIVED, LegStatus.READY_FOR_PICKUP,
        LegStatus.RESHIP_REQUESTED, LegStatus.RESHIP_COST_SENT, LegStatus.RESHIPPING,
        LegStatus.CLEAR, LegStatus.CLOSED,
    ]

    @property
    def in_transit(self) -> bool:
        """True once any confirmed leg has been received by its traveler
        (PACKAGE_RECEIVED or later) and that leg's travel date+time has
        passed — see PLAN-buyer-first-orders.md §7. Until a leg is received,
        travel hasn't actually started yet, so the order stays "Open"
        regardless of how the calendar date compares."""
        received_index = self._LEG_PROGRESS_ORDER.index(LegStatus.PACKAGE_RECEIVED)
        now = timezone.localtime()
        for leg in self.confirmed_legs:
            if leg.leg_status not in self._LEG_PROGRESS_ORDER:
                continue
            if self._LEG_PROGRESS_ORDER.index(leg.leg_status) < received_index:
                continue
            leg_dt = timezone.make_aware(datetime.combine(leg.travel_date, leg.travel_time or time.min))
            if leg_dt <= now:
                return True
        return False

    @property
    def home_section(self) -> str:
        """Which of the 3 home-page sections this order belongs on — purely a
        display classification, not a status (see PLAN-buyer-first-orders.md
        §7)."""
        if self.status == Status.CLOSED:
            return "closed"
        if self.in_transit:
            return "transit"
        return "open"

    def recompute_status(self) -> None:
        """Buyer-first orders don't set ``status`` directly once legs exist —
        it's a rollup of the legs' progress (see PLAN-buyer-first-orders.md
        §4). Call this after any offer/leg change. No-op for plan-first
        orders, which keep their own explicit workflow."""
        if self.plan_id:
            return
        legs = list(self.traveler_offers.all())
        confirmed = [l for l in legs if l.offer_status == OfferStatus.SELECTED]
        pending = [l for l in legs if l.offer_status == OfferStatus.PENDING]
        # A DROPOFF_MISSED leg is a dead end — ignore it in the rollup unless
        # every confirmed leg failed, in which case the order itself failed.
        live = [l for l in confirmed if l.leg_status != LegStatus.DROPOFF_MISSED]

        if confirmed and not live:
            new_status = Status.DROPOFF_MISSED
        elif live and all(l.leg_status == LegStatus.CLOSED for l in live):
            new_status = Status.CLOSED
        else:
            active = [l for l in live if l.leg_status not in (None, LegStatus.CLOSED)]
            if active:
                least = min(active, key=lambda l: self._LEG_PROGRESS_ORDER.index(l.leg_status))
                new_status = Status(least.leg_status)
            elif self.is_fully_matched:
                new_status = Status.TAKEN
            elif pending:
                new_status = Status.RESPONDED
            else:
                new_status = Status.OPEN

        if new_status != self.status:
            self.status = new_status
            self.save(update_fields=["status", "updated_at"])

    _BUYER_STATUS_LABELS = {
        "accepted": "Estimate Received",
        "deposit_paid": "W/f Actual Cost",
        "reship_requested": "Awaiting Cost Details",
        "reship_cost_sent": "Pay Reship Cost",
        "reshipping": "In Transit",
    }

    @property
    def buyer_status_display(self) -> str:
        """Status label shown to the buyer — differs from the traveler's label
        for certain statuses (e.g. 'Estimate Sent' → 'Estimate Received')."""
        if self.status == Status.ITEMS_PURCHASED:
            return self._items_purchased_date_label()
        return self._BUYER_STATUS_LABELS.get(self.status, self.get_status_display())

    @property
    def detail_status_label(self) -> str:
        """Status label shown in detail views and the traveler dashboard."""
        if self.status == Status.ITEMS_PURCHASED:
            return self._items_purchased_date_label()
        return self.get_status_display()

    def _items_purchased_date_label(self) -> str:
        """'Package Ready' before travel date, 'Package Carried' on/after."""
        today = timezone.now().date()
        return "Package Carried" if today >= self.plan.travel_date else "Package Ready"

    # --- Money ---
    @property
    def currency(self) -> str:
        return self.plan.shipment_currency if self.plan_id else self.settlement_currency

    def _idr_equivalent(self, amount: Decimal) -> "Decimal | None":
        """Convert a foreign-currency amount to IDR using the BCA TT Counter
        sell rate. Returns None when currency is already IDR or no active
        rate exists in the kurs table."""
        if self.currency == "IDR":
            return None
        try:
            rate = ExchangeRate.objects.get(code=self.currency, is_active=True)
            if not rate.sell_rate:
                return None
            return (amount * rate.sell_rate).quantize(TWO_PLACES)
        except ExchangeRate.DoesNotExist:
            return None

    @property
    def deposit_due_idr(self) -> "Decimal | None":
        return self._idr_equivalent(self.deposit_due)

    @property
    def unpaid_amount_idr(self) -> "Decimal | None":
        return self._idr_equivalent(self.unpaid_amount)

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
        rate = self.plan.shipment_cost_per_kg if self.plan_id else self.effective_cost_per_kg
        return self._q(self.estimated_weight_kg * rate)

    @property
    def shipment_cost(self) -> Decimal:
        """Actual shipment = traveler's final measured weight × rate."""
        rate = self.plan.shipment_cost_per_kg if self.plan_id else self.effective_cost_per_kg
        return self._q(self.actual_weight_kg * rate)

    @property
    def has_actual_weight(self) -> bool:
        return bool(self.actual_weight_kg and self.actual_weight_kg > 0)

    @property
    def effective_weight_kg(self) -> Decimal:
        """Weight counted toward plan capacity: actual once confirmed, else estimated."""
        return self.actual_weight_kg if self.has_actual_weight else self.estimated_weight_kg

    @property
    def refund_details_provided(self) -> bool:
        return bool(self.refund_account_no and self.refund_account_name)

    @property
    def items_actual_total_idr(self):
        return self._idr_equivalent(self.items_actual_total)

    @property
    def customs_invoice_available(self) -> bool:
        """True once the traveler has recorded actual purchases. Buyer-first
        orders have no separate purchase step — the buyer declares price for
        each item at posting time, so the invoice is available immediately."""
        if self.plan_id is None:
            return self.items.exists()
        unavailable = {
            Status.NEW, Status.REQUEST_RECEIVED, Status.ACCEPTED,
            Status.DEPOSIT_PAID, Status.REJECTED, Status.CANCELLED, Status.CLOSED,
        }
        return self.status not in unavailable

    # --- Quantities (Est = original request, Act = traveler's actuals) ---
    @property
    def est_qty_total(self) -> int:
        return sum(i.quantity for i in self.items.all())

    @property
    def act_qty_total(self) -> int:
        return sum(i.actual_quantity for i in self.items.all())

    # --- Margin (Est on estimated items, Act on actual items) ---
    # Buyer-first orders have no margin: the buyer already owns the items, so
    # there's no item markup — jastip's fee is entirely in the per-kg rate.
    @property
    def estimated_margin(self) -> Decimal:
        if not self.plan_id:
            return Decimal("0.00")
        return self._q(self.items_estimated_total * self.plan.margin_percent / Decimal("100"))

    @property
    def actual_margin(self) -> Decimal:
        if not self.plan_id:
            return Decimal("0.00")
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
        """Custom duty converted to the plan's invoice currency.

        BCA sell_rate = IDR per 1 unit of foreign currency.
        - Same currency → return as-is (rate = 1).
        - Duty in IDR, invoice in foreign → divide by sell_rate[invoice_ccy].
        - Duty in foreign, invoice in IDR → multiply by sell_rate[duty_ccy].
        Falls back to the stored amount when no rate is available.
        """
        if not self.custom_fare_amount:
            return Decimal("0.00")
        fare_ccy = self.custom_fare_currency or self.currency
        if fare_ccy == self.currency:
            return self._q(self.custom_fare_amount)
        if fare_ccy == "IDR" and self.currency != "IDR":
            # IDR duty → foreign invoice: ÷ sell_rate
            try:
                rate = ExchangeRate.objects.get(code=self.currency, is_active=True)
                if rate.sell_rate:
                    return (self.custom_fare_amount / rate.sell_rate).quantize(TWO_PLACES)
            except ExchangeRate.DoesNotExist:
                pass
        elif self.currency == "IDR" and fare_ccy != "IDR":
            # Foreign duty → IDR invoice: × sell_rate
            try:
                rate = ExchangeRate.objects.get(code=fare_ccy, is_active=True)
                if rate.sell_rate:
                    return (self.custom_fare_amount * rate.sell_rate).quantize(TWO_PLACES)
            except ExchangeRate.DoesNotExist:
                pass
        return self._q(self.custom_fare_amount)

    @property
    def custom_fare_needs_conversion(self) -> bool:
        return bool(
            self.custom_fare_amount
            and self.custom_fare_currency
            and self.custom_fare_currency != self.currency
        )

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


class TravelerOffer(models.Model):
    """A traveler's response to a buyer-first BuyRequest (order). Once selected
    by the buyer it becomes an independent "leg" — its own deposit, drop-off,
    and lifecycle — so a single order can be split across several travelers
    (partial fulfillment) without the legs blocking each other.
    """

    order = models.ForeignKey(BuyRequest, on_delete=models.CASCADE, related_name="traveler_offers")
    traveler = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="traveler_offers")

    ask_cost_per_kg = models.DecimalField(max_digits=12, decimal_places=2)
    avail_kg = models.DecimalField(
        max_digits=6, decimal_places=2, help_text="Traveler's declared spare capacity — shown publicly."
    )

    # Hidden from the buyer until this leg's deposit clears.
    drop_off_address = models.TextField(blank=True)
    drop_off_postal_code = models.CharField(max_length=20, blank=True)
    # Hidden until PACKAGE_ARRIVED and the buyer chooses Pickup (not Reship).
    pickup_address = models.TextField(blank=True)

    travel_date = models.DateField()
    travel_time = models.TimeField(null=True, blank=True)
    from_city = models.CharField(max_length=80)
    from_country = models.CharField(max_length=80)
    to_city = models.CharField(max_length=80)
    to_country = models.CharField(max_length=80)

    offer_status = models.CharField(max_length=10, choices=OfferStatus.choices, default=OfferStatus.PENDING)

    # Set once selected (offer_status=SELECTED); must be <= avail_kg.
    allocated_weight_kg = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    leg_status = models.CharField(max_length=24, choices=LegStatus.choices, null=True, blank=True)
    agreed_weight_kg = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Traveler's final measured weight at WEIGHT_VERIFIED — always authoritative.",
    )
    fulfillment_method = models.CharField(max_length=10, choices=FulfillmentMethod.choices, blank=True)

    # Reshipment — mirrors BuyRequest's reshipment_* fields, but per leg: a
    # partially-fulfilled order can have several legs each reshipping (or not)
    # independently.
    reshipment_address = models.TextField(blank=True)
    reshipment_cost_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    reshipment_cost_proof = models.ImageField(upload_to="leg_reshipment_costs/", blank=True, null=True, storage=webp_storage)
    reshipment_bank_name = models.CharField(max_length=120, blank=True)
    reshipment_bank_account_no = models.CharField(max_length=60, blank=True)
    reshipment_bank_account_name = models.CharField(max_length=120, blank=True)
    reshipment_proof = models.ImageField(upload_to="leg_reshipment_proofs/", blank=True, null=True, storage=webp_storage)
    awb_number = models.CharField(max_length=80, blank=True)
    awb_document = models.FileField(upload_to="leg_awb/", blank=True, null=True)

    # Buyer's bank details for receiving a refund on this leg, free text.
    refund_bank_details = models.TextField(blank=True)

    dropped_off_at = models.DateTimeField(null=True, blank=True)
    weight_verified_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True, help_text="When the buyer marked this leg Clear.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["order", "offer_status"])]

    def __str__(self):
        return f"Offer by {self.traveler} on {self.order.reference} ({self.get_offer_status_display()})"

    @property
    def route(self) -> str:
        return f"{self.from_city}, {self.from_country} → {self.to_city}, {self.to_country}"

    @property
    def drop_off_deadline(self):
        """Buyer must hand over the package by travel_date − 1 day."""
        from datetime import timedelta
        return self.travel_date - timedelta(days=1)

    @property
    def deposit_due(self) -> Decimal:
        """Deposit owed once selected: allocated weight × accepted ask rate."""
        return ((self.allocated_weight_kg or Decimal("0")) * self.ask_cost_per_kg).quantize(TWO_PLACES)

    @property
    def deposit_paid_amount(self) -> Decimal:
        if not hasattr(self, "transaction"):
            return Decimal("0.00")
        qs = self.transaction.payments.filter(
            direction=LegPayment.Direction.INBOUND,
            status=LegPayment.PaymentStatus.VERIFIED,
            kind=LegPayment.Kind.DEPOSIT,
        )
        return sum((p.amount for p in qs), Decimal("0.00")).quantize(TWO_PLACES)

    @property
    def deposit_verified(self) -> bool:
        return self.deposit_due > 0 and self.deposit_paid_amount >= self.deposit_due

    @property
    def address_revealed(self) -> bool:
        """Traveler's name and drop-off address stay hidden until the deposit clears."""
        return self.deposit_verified

    @property
    def pickup_address_revealed(self) -> bool:
        """Traveler's destination address stays hidden until Pickup is chosen."""
        return self.fulfillment_method == FulfillmentMethod.PICKUP

    @property
    def weight_delta(self) -> Decimal:
        """(final measured weight − allocated) × accepted ask rate. Positive =
        buyer owes a balance; negative = buyer overpaid (refund due)."""
        final = self.agreed_weight_kg if self.agreed_weight_kg is not None else (self.allocated_weight_kg or Decimal("0"))
        delta_kg = final - (self.allocated_weight_kg or Decimal("0"))
        return (delta_kg * self.ask_cost_per_kg).quantize(TWO_PLACES)

    @property
    def extra_due(self) -> Decimal:
        """Amount the buyer still owes (final weight came in heavier than allocated)."""
        d = self.weight_delta
        return d if d > 0 else Decimal("0.00")

    @property
    def refund_due(self) -> Decimal:
        """Amount to refund the buyer (final weight came in lighter than allocated)."""
        d = self.weight_delta
        return -d if d < 0 else Decimal("0.00")

    @property
    def balance_paid_amount(self) -> Decimal:
        if not hasattr(self, "transaction"):
            return Decimal("0.00")
        qs = self.transaction.payments.filter(
            direction=LegPayment.Direction.INBOUND,
            status=LegPayment.PaymentStatus.VERIFIED,
            kind=LegPayment.Kind.BALANCE,
        )
        return sum((p.amount for p in qs), Decimal("0.00")).quantize(TWO_PLACES)

    @property
    def total_refunded(self) -> Decimal:
        if not hasattr(self, "transaction"):
            return Decimal("0.00")
        qs = self.transaction.payments.filter(
            direction=LegPayment.Direction.OUTBOUND,
            status=LegPayment.PaymentStatus.VERIFIED,
            kind=LegPayment.Kind.REFUND,
        )
        return sum((p.amount for p in qs), Decimal("0.00")).quantize(TWO_PLACES)

    @property
    def balance_settled(self) -> bool:
        """True once the weight-delta is settled in whichever direction it goes."""
        if self.extra_due > 0:
            return self.balance_paid_amount >= self.extra_due
        if self.refund_due > 0:
            return self.total_refunded >= self.refund_due
        return True

    @property
    def dropoff_refund_amount(self) -> Decimal:
        """Outstanding deposit refund recorded after a missed drop-off
        (created by the expire_missed_dropoffs cron) — verified or not, for
        buyer-facing display. Unrelated to the weight-delta refund_due above."""
        if not hasattr(self, "transaction"):
            return Decimal("0.00")
        qs = self.transaction.payments.filter(
            direction=LegPayment.Direction.OUTBOUND, kind=LegPayment.Kind.REFUND,
        )
        return sum((p.amount for p in qs), Decimal("0.00")).quantize(TWO_PLACES)


class LegTransaction(models.Model):
    """Settlement record for a single confirmed leg (selected TravelerOffer)
    of a buyer-first order. Kept independent of `Transaction` — a partially
    fulfilled order has one deposit/payout per traveler, not one for the
    whole order, so it can't share the BuyRequest-level transaction."""

    leg = models.OneToOneField(TravelerOffer, on_delete=models.CASCADE, related_name="transaction")
    commission_percent = models.DecimalField(
        max_digits=5, decimal_places=2,
        default=Decimal(str(settings.PLATFORM_COMMISSION_PERCENT)),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Leg TX for {self.leg.order.reference} ({self.leg.traveler})"

    @property
    def currency(self) -> str:
        return self.leg.order.currency

    @property
    def gross_amount(self) -> Decimal:
        """Total amount earned by the traveler for this leg, based on final weight."""
        leg = self.leg
        weight = leg.agreed_weight_kg if leg.agreed_weight_kg is not None else leg.allocated_weight_kg
        return ((weight or Decimal("0")) * leg.ask_cost_per_kg).quantize(TWO_PLACES)

    @property
    def commission_amount(self) -> Decimal:
        return (self.gross_amount * self.commission_percent / Decimal("100")).quantize(TWO_PLACES)

    @property
    def payout_to_traveler(self) -> Decimal:
        return (self.gross_amount - self.commission_amount).quantize(TWO_PLACES)


class LegPayment(models.Model):
    """A single money movement against a leg's deposit. Mirrors `Payment`'s
    shape (same kinds of choices) but kept independent of it."""

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound (to admin)"
        OUTBOUND = "outbound", "Outbound (from admin)"

    class Kind(models.TextChoices):
        DEPOSIT = "deposit", "Traveler deposit"
        BALANCE = "balance", "Weight-delta balance"
        PAYOUT = "payout", "Payout to traveler"
        REFUND = "refund", "Refund"

    class Method(models.TextChoices):
        MANUAL = "manual", "Manual transfer"
        GATEWAY = "gateway", "Payment gateway"

    class PaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified / Paid"
        FAILED = "failed", "Failed"

    transaction = models.ForeignKey(LegTransaction, on_delete=models.CASCADE, related_name="payments")
    direction = models.CharField(max_length=10, choices=Direction.choices)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.MANUAL)

    currency = models.CharField(max_length=3, choices=Currency.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    proof = models.ImageField(upload_to="leg_payments/proof/", blank=True, null=True, storage=webp_storage)

    verified_by = models.ForeignKey(
        USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="verified_leg_payments"
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
    photo = models.ImageField(upload_to="items/requested/", blank=True, null=True, storage=webp_storage)

    # Set by the traveler when reviewing the request ("update the cost fields").
    estimated_unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    # Set by the traveler after actually purchasing.
    actual_quantity = models.PositiveSmallIntegerField(default=0, help_text="Quantity actually purchased.")
    actual_unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    purchase_photo = models.ImageField(upload_to="items/purchased/", blank=True, null=True, storage=webp_storage)
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
    def actual_line_total_idr(self):
        return self.request._idr_equivalent(self.actual_line_total)

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

    @property
    def payout_to_traveler_idr(self) -> "Decimal | None":
        return self.request._idr_equivalent(self.payout_to_traveler)


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
    proof = models.ImageField(upload_to="payments/proof/", blank=True, null=True, storage=webp_storage)

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
    apply_to_countries = models.TextField(
        blank=True, default="",
        help_text="Comma-separated country names (matching the order form's country "
                   "list, e.g. \"United States, Singapore\") that should settle in this "
                   "currency. Buyer-first orders fall back to IDR if no match is found.",
    )
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sequence", "code"]

    def __str__(self):
        return f"{self.code} — sell {self.sell_rate:,.2f}"

    @classmethod
    def currency_for_country(cls, country: str) -> str:
        """Resolve a buyer-first order's settlement currency from its From
        Country, via each active rate's admin-assigned `apply_to_countries`
        list. Falls back to IDR when nothing matches."""
        if country:
            needle = country.strip().lower()
            for rate in cls.objects.filter(is_active=True).exclude(apply_to_countries=""):
                names = (n.strip().lower() for n in rate.apply_to_countries.split(","))
                if needle in names:
                    return rate.code
        return Currency.IDR


class Message(models.Model):
    """A chat message between the buyer and traveler on a request (admin can
    read all threads for oversight)."""

    request = models.ForeignKey(BuyRequest, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="sent_messages")
    body = models.TextField()
    photo = models.ImageField(upload_to="chat/", blank=True, null=True, storage=webp_storage)
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
