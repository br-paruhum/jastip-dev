from datetime import datetime, time, timedelta
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
    COUNTRY_CHOICES,
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


def platform_fee_floor(currency: str) -> Decimal:
    """Admin-set minimum platform fee, in IDR. The 2.5% commission is floored to
    this amount. Only IDR-denominated commissions are floored — proxy orders
    always settle in IDR, and an IDR floor is meaningless against a foreign
    amount. Lazy import keeps apps.pages out of this module's import graph."""
    if currency != "IDR":
        return Decimal("0.00")
    from apps.pages.models import SiteSettings
    return SiteSettings.load().platform_fee_min_idr or Decimal("0.00")

# Home-page marketplace listing window. A posting locks (shown "Closed",
# action "Locked", no longer joinable) once we are within LISTING_LOCK_HOURS of
# its anchor moment, and drops off the home page entirely once LISTING_LOCK_HOURS
# past it. Display-only — the real lifecycle status is untouched, and the owner's
# dashboard keeps the row until it actually closes.
LISTING_LOCK_HOURS = 24
# Spare-baggage travel plans stay on the home board until this many days after
# the travel date, then drop off and are auto-closed (see close_overdue_plans).
PLAN_BOARD_RETENTION_DAYS = 2


class ListingTimingMixin:
    """Shared travel-time listing rules for home-page tables. Subclasses provide
    ``_listing_anchor_dt`` (an aware datetime, or None)."""

    @property
    def listing_locked(self) -> bool:
        dt = self._listing_anchor_dt
        return bool(dt and timezone.now() >= dt - timedelta(hours=LISTING_LOCK_HOURS))

    @property
    def listing_expired(self) -> bool:
        dt = self._listing_anchor_dt
        return bool(dt and timezone.now() >= dt + timedelta(hours=LISTING_LOCK_HOURS))


class TravelPlan(ListingTimingMixin, models.Model):
    """A carrier's offer: a trip with spare luggage capacity."""

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
        help_text="Carrier only carries luggage space — not willing to act as a proxy buyer.",
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
    def _listing_anchor_dt(self):
        """Departure moment: travel_date at travel_time (midnight if no time set)."""
        if not self.travel_date:
            return None
        return timezone.make_aware(datetime.combine(self.travel_date, self.travel_time or time.min))

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_PLAN_STATUSES

    @property
    def can_edit(self) -> bool:
        """Owner may edit or cancel only while the plan is still open, no buyer
        has ordered on it yet (counterparty hasn't responded), and it isn't
        within 24h of departure (listing locked)."""
        return self.is_open and not self.active_requests and not self.listing_locked

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
    def board_expired(self) -> bool:
        """Drop from the spare-baggage board PLAN_BOARD_RETENTION_DAYS after the
        travel date (then close_overdue_plans sets the status to Closed)."""
        return bool(
            self.travel_date
            and timezone.now().date() >= self.travel_date + timedelta(days=PLAN_BOARD_RETENTION_DAYS)
        )

    @property
    def is_closed(self) -> bool:
        return self.status == Status.CLOSED

    @property
    def status_tone(self) -> str:
        return STATUS_TONE.get(self.status, "info")

    @property
    def route(self) -> str:
        return f"{self.from_city}, {self.from_country} → {self.to_city}, {self.to_country}"

    @property
    def handover_deadline(self):
        """Cargo: latest date the buyer should hand the package to the carrier.
        The day before travel when departing after noon, otherwise two days
        before (a missing time is treated as 12:00, i.e. the two-day case)."""
        from datetime import timedelta

        if not self.travel_date:
            return None
        days = 1 if (self.travel_time and self.travel_time > time(12, 0)) else 2
        return self.travel_date - timedelta(days=days)

    @property
    def type_label(self) -> str:
        """Carrier's transaction type for this plan (see PLAN-flow-taxonomy.md)."""
        return "Carrier Only" if self.carrier_only else "Proxy Buyer"

    @property
    def active_request(self):
        """The buy request currently driving this plan's lifecycle, if any."""
        return self.buy_requests.exclude(status=Status.REJECTED).order_by("-created_at").first()

    @property
    def utilized_weight_kg(self) -> Decimal:
        """Spare weight already taken up by current orders.

        Counts every non-rejected buy request, using its actual weight once the
        buyer has confirmed it (set at pickup), otherwise the carrier's
        estimate. So the figure starts showing as soon as an estimate is entered
        and is automatically corrected to the actual weight later — giving the
        carrier no benefit from over-estimating.

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
        """Spare weight still available to fill (never negative for display).
        Surplus = total capacity − weight taken by buyers who joined this plan."""
        remaining = self.available_weight_kg - self.utilized_weight_kg
        return remaining if remaining > 0 else Decimal("0").quantize(TWO_PLACES)


class BuyRequest(ListingTimingMixin, models.Model):
    """A buyer 'orders' a travel plan and lists items to purchase — OR, when
    ``plan`` is null, a buyer-first order posted with no carrier yet (see
    PLAN-buyer-first-orders.md). Matching carriers respond via TravelerOffer;
    once selected, each offer becomes an independent "leg" with its own
    deposit and lifecycle (partial fulfillment can split one order across
    several carriers).

    This carries the transaction lifecycle from 'Ordered' onward.
    """

    plan = models.ForeignKey(
        TravelPlan, on_delete=models.CASCADE, related_name="buy_requests", null=True, blank=True
    )
    buyer = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="buy_requests")
    # Flow-1 (proxy buying): the admin-curated proxy buyer the buyer chose to
    # source the products. Null for cargo orders and legacy/plan-first requests.
    proxy_buyer = models.ForeignKey(
        "ProxyBuyer", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
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
        default=False, help_text="Allow this order to be split across multiple carriers."
    )
    cargo_only = models.BooleanField(
        default=False,
        help_text="Buyer-first only: buyer already has the goods and needs a Carrier to "
                  "carry them (no proxy purchasing). False = Products Buyer; True = Cargo Buyer.",
    )
    # Flow-1 (proxy buying): the proxy's markup on the products, set when they
    # send their estimate. Drives the invoice Margin row, totals, and deposit.
    proxy_margin_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"),
        help_text="Proxy buyer's margin % on the products (buyer-first Products only).",
    )
    # Proxy disbursement: a SINGLE payment of the full net (products + margin −
    # fee), released by admin once the buyer has received/cleared the package.
    proxy_disbursed_at = models.DateTimeField(null=True, blank=True)
    proxy_disbursement_proof = models.ImageField(upload_to="disbursement_proofs/", blank=True, null=True, storage=webp_storage)
    # DEPRECATED (old 50/50 split — kept dormant for historical rows).
    proxy_first_disbursed_at = models.DateTimeField(null=True, blank=True)
    proxy_second_disbursed_at = models.DateTimeField(null=True, blank=True)
    proxy_first_disbursement_proof = models.ImageField(upload_to="disbursement_proofs/", blank=True, null=True, storage=webp_storage)
    proxy_second_disbursement_proof = models.ImageField(upload_to="disbursement_proofs/", blank=True, null=True, storage=webp_storage)
    # Flow-1 carrier payout actually released by admin (the carry fee + customs,
    # less the platform fee). Set from the admin "mark paid" action, not the
    # workflow — so the traveler's "Your Payout" status reflects real money out.
    traveler_paid_at = models.DateTimeField(null=True, blank=True)
    traveler_payout_proof = models.ImageField(upload_to="disbursement_proofs/", blank=True, null=True, storage=webp_storage)

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
    # Pickup path: the buyer chose local pickup (vs reshipment) at Ready-for-Pickup.
    # The package is still with the traveler until the buyer confirms receipt, which
    # then clears the order. Distinguishes "chose pickup" from "received it".
    pickup_selected = models.BooleanField(default=False)
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
            self.reference = self._generate_reference()
        super().save(*args, **kwargs)

    def _generate_reference(self) -> str:
        """PRX-YYMMDDXXX — branded prefix + today's date + 3 random alphanumerics.
        Retries on the rare same-day collision (reference is unique)."""
        import secrets
        import string
        date_part = timezone.now().strftime("%y%m%d")
        alphabet = string.ascii_uppercase + string.digits
        while True:
            suffix = "".join(secrets.choice(alphabet) for _ in range(3))
            ref = f"PRX-{date_part}{suffix}"
            if not type(self).objects.filter(reference=ref).exists():
                return ref

    def get_absolute_url(self):
        # Transaction detail is embedded as an in-page panel on the profile page.
        return reverse("accounts:profile") + f"?order={self.pk}#order-detail"

    @property
    def status_tone(self) -> str:
        return STATUS_TONE.get(self.status, "info")

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

    # --- Transaction type (see PLAN-flow-taxonomy.md). Plan-first inherits the
    # plan's carrier_only; buyer-first uses this order's own cargo_only. ---
    @property
    def is_cargo(self) -> bool:
        """True = carrying (cargo) transaction; False = proxy-buying (products)."""
        if self.plan_id:
            return self.plan.carrier_only
        return self.cargo_only

    @property
    def is_proxy_buyer_first(self) -> bool:
        """True for a Flow-1 order: a buyer-first Products order sourced by a
        separate Proxy Buyer (NOT the carrier). The carrier only CARRIES the
        goods, so for the carrier's economics this behaves exactly like cargo;
        the product+margin is disbursed to the proxy instead."""
        return self.plan_id is None and self.proxy_buyer_id is not None and not self.cargo_only

    @property
    def carrier_charges_carry_only(self) -> bool:
        """The carrier is paid the carry fee (+ reimbursable customs) only — true
        for cargo orders and for Flow-1 proxy orders (sourcing is the proxy's)."""
        return self.is_cargo or self.is_proxy_buyer_first

    @property
    def actor_label(self) -> str:
        """The buyer's type label for this order."""
        return "Cargo Buyer" if self.is_cargo else "Products Buyer"

    @property
    def counterparty_label(self) -> str:
        """The carrier's type label for this order."""
        return "Carrier Only" if self.is_cargo else "Proxy Buyer"

    @property
    def route(self) -> str:
        if self.plan_id:
            return self.plan.route
        return f"{self.from_city}, {self.from_country} → {self.to_city}, {self.to_country}"

    @property
    def traveler_user(self):
        """The carrier driving this order. Plan-first: the plan's carrier.
        Buyer-first: only meaningful once exactly one leg is confirmed — a
        partially-fulfilled order has several carriers, so this returns None
        and callers should iterate ``confirmed_legs`` instead."""
        if self.plan_id:
            return self.plan.traveler
        legs = self.confirmed_legs
        if len(legs) == 1:
            return legs[0].traveler
        # Flow-1 proxy order: no leg is created, but there's a single live offer.
        live = [o for o in self.traveler_offers.all()
                if o.offer_status in (OfferStatus.PENDING, OfferStatus.SELECTED)]
        return live[0].traveler if len(live) == 1 else None

    @property
    def chat_participants(self) -> list:
        """Users in this order's message thread: the buyer, the carrier (plan's
        or the Flow-1 live-offer's), and the assigned proxy buyer. Deduped, no
        None. Admin oversight is handled separately."""
        users = [self.buyer]
        if self.plan_id:
            users.append(self.plan.traveler)
        else:
            users.append(self.traveler_user)
            if self.proxy_buyer_id:
                users.append(self.proxy_buyer.user)
        seen = []
        for u in users:
            if u and u not in seen:
                seen.append(u)
        return seen

    def is_chat_participant(self, user) -> bool:
        """True if `user` may read/post in this order's message thread."""
        if not user or not user.is_authenticated:
            return False
        return any(user.id == p.id for p in self.chat_participants)

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
        if not self.is_cargo:
            # Products (FCFS single proxy): use the live proxy offer's quoted rate
            # so the buyer sees the full estimated shipment before accepting it.
            live = [
                o for o in self.traveler_offers.all()
                if o.offer_status in (OfferStatus.PENDING, OfferStatus.SELECTED)
            ]
            if len(live) == 1:
                return live[0].ask_cost_per_kg
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
    def pending_weight_kg(self) -> Decimal:
        """Cargo plan-first request: the requested weight still awaiting the
        carrier's acceptance (Bid − Accepted, from the buyer's point of view).
        Drops to 0 the moment the carrier accepts."""
        if self.status == Status.REQUEST_RECEIVED:
            return self._q(self.estimated_weight_kg)
        return Decimal("0")

    @property
    def is_fully_matched(self) -> bool:
        return bool(self.confirmed_legs) and self.total_allocated_weight_kg >= self.bid_weight_kg

    @property
    def is_multi_leg_cargo(self) -> bool:
        """Cargo carried by more than one carrier — goods must be split per leg
        (each carrier clears customs separately on a different flight)."""
        return self.is_cargo and len(self.confirmed_legs) > 1

    @property
    def items_fully_assigned(self) -> bool:
        """Every declared unit is assigned to a carrier (per-leg customs ready)."""
        return all(i.unallocated_quantity == 0 for i in self.items.all())

    @property
    def needs_item_assignment(self) -> bool:
        """Buyer still has to split the goods across the carriers."""
        return self.is_multi_leg_cargo and not self.items_fully_assigned

    @property
    def assignment_locked(self) -> bool:
        """Once any carrier has taken custody (drop-off), the goods are physically
        split — the buyer can no longer re-assign."""
        return any(l.leg_status for l in self.confirmed_legs)

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
        """True once any confirmed leg has been received by its carrier
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

    @property
    def _listing_anchor_dt(self):
        """Listing anchor for a buyer-first order = the offer deadline
        (max_acceptable_date, at midnight). Plan-first orders aren't listed on
        the home page, so they have no anchor."""
        if self.plan_id or not self.max_acceptable_date:
            return None
        return timezone.make_aware(datetime.combine(self.max_acceptable_date, time.min))

    @property
    def can_edit(self) -> bool:
        """Buyer may edit/cancel their order only before the counterparty engages
        and outside the 24h listing lock.
        - Plan-first (incl. cargo on a carrier plan): only while the carrier
          hasn't acted (status REQUEST_RECEIVED) and the plan isn't within 24h
          of departure.
        - Buyer-first: only while OPEN, with no live (pending/selected) carrier
          offer, and not past the 24h deadline lock."""
        if self.plan_id:
            return self.status == Status.REQUEST_RECEIVED and not self.plan.listing_locked
        if self.status != Status.OPEN or self.listing_locked:
            return False
        live = {OfferStatus.PENDING, OfferStatus.SELECTED}
        return not any(o.offer_status in live for o in self.traveler_offers.all())

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

    # Cargo (Carrier) flow has no estimate/purchase, so some statuses read
    # differently for both sides (see PLAN-flow-taxonomy.md Flow 2).
    _CARGO_STATUS_LABELS = {
        "request_received": "W/f Acceptance",
        "accepted": "Accepted",
        "deposit_paid": "Deposit Verified",
    }

    def _cargo_status_label(self):
        """Cargo-specific badge label, or None when not a special cargo case.
        Once a payment proof is uploaded (deposit while 'accepted', or the
        balance while 'package_arrived'), the badge reads 'W/f Fund
        Verification' until admin verifies it."""
        if not self.is_cargo:
            return None
        if self.status == Status.ACCEPTED and self.deposit_pending:
            return "W/f Fund Verification"
        if self.status == Status.PACKAGE_ARRIVED and self.balance_pending:
            return "W/f Fund Verification"
        return self._CARGO_STATUS_LABELS.get(self.status)

    @property
    def buyer_status_display(self) -> str:
        """Status label shown to the buyer — differs from the carrier's label
        for certain statuses (e.g. 'Estimate Sent' → 'Estimate Received')."""
        # Proxy (Flow-1) order: the buyer is waiting on the proxy's estimate, not
        # a traveler offer.
        if not self.is_cargo and self.plan_id is None:
            if self.status == Status.OPEN:
                return "Request Send"
            if self.status == Status.RESPONDED:
                # The proxy has just sent its estimate — the buyer's next move is to
                # review and pay. A carrier offer (if any) comes a step later, so
                # don't surface "Offer Received" / "Looking for Carrier" here yet.
                return "Estimate Sent"
            if self.status == Status.ACCEPTED:
                # Tentative booking until the deposit is verified (then it locks
                # and the traveler's spare baggage is advertised).
                return "Temp Book — Verifying" if self.deposit_pending else "Temp Book — Deposit Due"
        if self.status == Status.ITEMS_PURCHASED:
            return self._items_purchased_date_label()
        return self._cargo_status_label() or self._BUYER_STATUS_LABELS.get(
            self.status, self.get_status_display()
        )

    @property
    def detail_status_label(self) -> str:
        """Status label shown in detail views and the carrier dashboard."""
        if self.status == Status.ITEMS_PURCHASED:
            return self._items_purchased_date_label()
        return self._cargo_status_label() or self.get_status_display()

    def _items_purchased_date_label(self) -> str:
        """'Package Ready' before travel date, 'Package Carried' on/after. For
        buyer-first orders the travel date lives on the live carrier offer (no
        plan); fall back to 'Package Ready' if none is set yet."""
        today = timezone.now().date()
        if self.plan_id:
            travel_date = self.plan.travel_date
        else:
            offer = self.traveler_offers.filter(
                offer_status__in=[OfferStatus.PENDING, OfferStatus.SELECTED]
            ).first()
            travel_date = offer.travel_date if offer else None
        return "Package Carried" if travel_date and today >= travel_date else "Package Ready"

    # --- Money ---
    @property
    def currency(self) -> str:
        return self.plan.shipment_currency if self.plan_id else self.settlement_currency

    @property
    def destination_currency(self) -> str:
        """Currency at the destination country — customs duty is always paid here
        (mirrors shipment always being in the origin currency). Resolved via the
        fx/kurs table; falls back to IDR."""
        country = self.to_country or (self.plan.to_country if self.plan_id else "")
        return ExchangeRate.currency_for_country(country)

    @property
    def proxy_traveler(self):
        """The carrier/proxy for UI display: the plan's carrier (plan-first) or
        the single live offer's carrier (buyer-first Products, FCFS)."""
        if self.plan_id:
            return self.plan.traveler
        live = [o for o in self.traveler_offers.all()
                if o.offer_status in (OfferStatus.PENDING, OfferStatus.SELECTED)]
        return live[0].traveler if len(live) == 1 else None

    @property
    def carry_offer(self):
        """The single live (pending/selected) carry offer for a buyer-first
        proxy order — the source of the travel date/route on the invoice."""
        if self.plan_id:
            return None
        live = [o for o in self.traveler_offers.all()
                if o.offer_status in (OfferStatus.PENDING, OfferStatus.SELECTED)]
        return live[0] if len(live) == 1 else None

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

    def _amount_in(self, amount: Decimal, target: str) -> "Decimal | None":
        """Convert an amount from this order's (origin) currency to `target`,
        cross-rated through IDR via the BCA sell rate (sell_rate = IDR per 1
        foreign unit). Returns None when `target` equals the order currency or a
        needed rate is missing, so a template can simply hide the second line."""
        src = self.currency
        if amount is None or not target or target == src:
            return None
        amt = Decimal(amount)
        # 1) origin currency -> IDR
        if src == Currency.IDR:
            idr = amt
        else:
            rate = ExchangeRate.objects.filter(code=src, is_active=True).first()
            if not rate or not rate.sell_rate:
                return None
            idr = amt * rate.sell_rate
        # 2) IDR -> target currency
        if target == Currency.IDR:
            return idr.quantize(TWO_PLACES)
        rate = ExchangeRate.objects.filter(code=target, is_active=True).first()
        if not rate or not rate.sell_rate:
            return None
        return (idr / rate.sell_rate).quantize(TWO_PLACES)

    def _settlement(self, amount: Decimal) -> dict:
        """The buyer always settles in IDR (the platform holds one IDR account).
        Returns what to charge plus a foreign-currency figure shown for the
        buyer's reference only — they convert at their bank and still pay in IDR:
          idr        — amount to actually transfer (Decimal, IDR)
          ref_ccy    — foreign currency code for the reference line ("" if none)
          ref_amount — that foreign equivalent (Decimal) or None
        The reference is the order's own currency when it's foreign; otherwise the
        destination-country currency, so an IDR-origin order still hints the
        buyer's local cost (e.g. EUR for a German buyer)."""
        if self.currency == Currency.IDR:
            ref = self._amount_in(amount, self.destination_currency)
            rate = (amount / ref) if ref else None  # IDR per 1 ref unit
            return {"idr": amount,
                    "ref_ccy": self.destination_currency if ref is not None else "",
                    "ref_amount": ref, "rate": rate}
        idr = self._idr_equivalent(amount)
        rate = None  # BCA TT sell rate actually used (IDR per 1 unit of order ccy)
        try:
            er = ExchangeRate.objects.get(code=self.currency, is_active=True)
            rate = er.sell_rate or None
        except ExchangeRate.DoesNotExist:
            pass
        return {"idr": idr if idr is not None else amount,
                "ref_ccy": self.currency, "ref_amount": amount, "rate": rate}

    @property
    def deposit_settlement(self) -> dict:
        return self._settlement(self.deposit_due)

    @property
    def balance_settlement(self) -> dict:
        return self._settlement(self.balance_extra_due)

    @property
    def traveler_payout_ready(self) -> bool:
        """Flow-1 (buyer-first proxy): the carrier's payout figures are final once
        the order has arrived — the offer never enters the leg lifecycle, so the
        offer-level `payout_ready` (leg_status-based) stays False here."""
        return self.is_proxy_buyer_first and self.status in {
            Status.PACKAGE_ARRIVED, Status.READY_FOR_PICKUP, Status.RESHIP_REQUESTED,
            Status.RESHIP_COST_SENT, Status.RESHIPPING, Status.CLEAR, Status.CLOSED,
        }

    # --- Flow-1 disbursement eligibility (admin releases the actual money) ----
    # The proxy's 1st 50% becomes payable once the traveler takes custody
    # (handover); the 2nd 50% and the carrier payout only once the buyer clears.
    _POST_HANDOVER_STATUSES = {
        Status.PACKAGE_RECEIVED, Status.PACKAGE_ARRIVED, Status.READY_FOR_PICKUP,
        Status.RESHIP_REQUESTED, Status.RESHIP_COST_SENT, Status.RESHIPPING,
        Status.CLEAR, Status.CLOSED,
    }
    _CLEARED_STATUSES = {Status.CLEAR, Status.CLOSED}

    @property
    def proxy_disbursable(self) -> bool:
        """The single proxy disbursement is eligible to release once the buyer has
        received/cleared the package (full net, not necessarily paid yet)."""
        return self.is_proxy_buyer_first and self.status in self._CLEARED_STATUSES

    @property
    def proxy_first_disbursable(self) -> bool:
        """1st 50% is eligible to release (handover done), not necessarily paid."""
        return self.is_proxy_buyer_first and self.status in self._POST_HANDOVER_STATUSES

    @property
    def proxy_second_disbursable(self) -> bool:
        """2nd 50% is eligible to release (buyer cleared), not necessarily paid."""
        return self.is_proxy_buyer_first and self.status in self._CLEARED_STATUSES

    @property
    def traveler_payout_disbursable(self) -> bool:
        """Order-level carrier payout is eligible to release (buyer cleared), not
        yet paid. Applies to the single-carrier flows — Flow-1 proxy and any
        plan-first order — but NOT buyer-first cargo (that pays per leg)."""
        return (self.is_proxy_buyer_first or self.plan_id is not None) and self.status in self._CLEARED_STATUSES

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
        """Shipment on the carrier's estimated weight (drives the deposit)."""
        rate = self.plan.shipment_cost_per_kg if self.plan_id else self.effective_cost_per_kg
        return self._q(self.estimated_weight_kg * rate)

    @property
    def shipment_cost(self) -> Decimal:
        """Actual shipment = carrier's final measured weight × rate."""
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
        """True once the carrier has recorded actual purchases. Buyer-first
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
    # Plan-first proxy buying applies the plan's margin %. Buyer-first Products
    # (Flow-1) applies the proxy buyer's per-order margin %. Cargo orders have no
    # margin (proxy_margin_percent stays 0).
    @property
    def margin_percent(self) -> Decimal:
        """The margin rate applied to this order's products: the plan's rate for
        plan-first proxy buying, else the proxy buyer's per-order rate (Flow-1).
        Cargo orders have no margin (0)."""
        return self.plan.margin_percent if self.plan_id else self.proxy_margin_percent

    @property
    def estimated_margin(self) -> Decimal:
        pct = self.margin_percent
        return self._q(self.items_estimated_total * pct / Decimal("100"))

    @property
    def actual_margin(self) -> Decimal:
        return self._q(self.items_actual_total * self.margin_percent / Decimal("100"))

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
        """Payment fixed at acceptance. Cargo (Carrier) = the full carry fee
        (weight × rate) — the carrier buys nothing. Proxy buying (Flow-1) = 100%
        of estimated products + margin PLUS 100% of the estimated shipment cost
        (Task 2 — full payment upfront). Only the customs duty and any actual-vs-
        estimate difference remain for the arrival balance."""
        if self.is_cargo:
            return self.estimated_shipment_cost
        return self._q(
            self.items_estimated_total + self.estimated_margin
            + self.estimated_shipment_cost
        )

    @property
    def effective_shipment_cost(self) -> Decimal:
        """Carry fee charged: actual (measured) weight × rate once verified, else the
        estimate fixed at acceptance."""
        return self.shipment_cost if self.has_actual_weight else self.estimated_shipment_cost

    @property
    def cargo_charge_total(self) -> Decimal:
        """Cargo (Carrier): what the buyer actually pays = carry fee + reimbursable
        customs. The declared item value is NOT charged — the buyer owns the goods."""
        return self._q(self.effective_shipment_cost + self.actual_custom)

    @property
    def cargo_balance_due(self) -> Decimal:
        """Cargo outstanding after the deposit — typically the customs reimbursement."""
        return self._q(self.cargo_charge_total - self.deposit_paid_amount)

    @property
    def balance_due_now(self) -> Decimal:
        """Balance the buyer settles at Package Arrived. Cargo (Carrier): carry fee
        on the actual weight + reimbursable customs − deposit already paid (the
        declared item value is never billed). Proxy buying: the actual invoice
        less everything paid. Positive = buyer owes; negative = refund owed."""
        return self.cargo_balance_due if self.is_cargo else self.unpaid_amount

    @property
    def balance_due_now_idr(self) -> "Decimal | None":
        return self._idr_equivalent(self.balance_due_now)

    @property
    def balance_extra_due(self) -> Decimal:
        """Outstanding balance the buyer still owes at arrival (0 if none)."""
        b = self.balance_due_now
        return b if b > 0 else Decimal("0.00")

    @property
    def balance_refund_due(self) -> Decimal:
        """Overpaid amount to refund the buyer at arrival (0 if none)."""
        b = self.balance_due_now
        return -b if b < 0 else Decimal("0.00")

    # --- Proxy buyer disbursement (Flow-1) -------------------------------------
    # The proxy fronted 100% of the product cost and earns the margin. The
    # platform disburses (products + margin − fee) to them, but holds it back as
    # buyer protection: 50% at handover, 50% at buyer clear.
    @property
    def proxy_gross(self) -> Decimal:
        """What the proxy is owed before the platform fee: actual products + margin."""
        return self._q(self.items_actual_total + self.actual_margin)

    @property
    def proxy_commission(self) -> Decimal:
        """Platform fee withheld from the proxy's disbursement (same 2.5% rate as
        the carrier's carry fee), floored to the admin-set IDR minimum. Withheld
        entirely from the proxy's final (second) disbursement half."""
        pct = Decimal(str(settings.PLATFORM_COMMISSION_PERCENT))
        return self._q(max(self.proxy_gross * pct / Decimal("100"),
                           platform_fee_floor(self.currency)))

    @property
    def proxy_disbursement_total(self) -> Decimal:
        """Net amount the platform pays the proxy across both halves."""
        return self._q(self.proxy_gross - self.proxy_commission)

    @property
    def proxy_disbursement_first_half(self) -> Decimal:
        """Released to the proxy when the carrier takes custody (handover). Pays
        out half the gross with NO platform fee deducted — the full fee is
        withheld from the final (second) half instead."""
        return self._q(self.proxy_gross * Decimal("0.5"))

    @property
    def proxy_disbursement_second_half(self) -> Decimal:
        """Held until the buyer clears the package; the remainder after the first
        half, net of the full platform fee."""
        return self._q(self.proxy_disbursement_total - self.proxy_disbursement_first_half)

    @property
    def proxy_disbursed_amount(self) -> Decimal:
        """How much of the disbursement has actually been released so far."""
        total = Decimal("0.00")
        if self.proxy_first_disbursed_at:
            total += self.proxy_disbursement_first_half
        if self.proxy_second_disbursed_at:
            total += self.proxy_disbursement_second_half
        return self._q(total)

    @property
    def proxy_actuals_editable(self) -> bool:
        """The proxy may enter actual costs at Deposit Paid and keep revising them
        at Package Ready, until they hand the goods to the carrier (Package
        Received). After handover the cost is fixed — it drives the buyer's arrival
        balance."""
        return self.is_proxy_buyer_first and self.status in {
            Status.DEPOSIT_PAID, Status.ITEMS_PURCHASED,
        }

    @property
    def customs_phase(self) -> bool:
        """Flow-1: the goods are purchased and being carried, so the customs
        invoice is now meaningful for the carrier (to declare at the border)."""
        return self.is_proxy_buyer_first and self.status in {
            Status.ITEMS_PURCHASED, Status.PACKAGE_RECEIVED,
            Status.PACKAGE_ARRIVED, Status.READY_FOR_PICKUP, Status.CLEAR,
        }

    @property
    def invoice_unpaid_overpaid(self) -> Decimal:
        """Invoice statement line: actual Total Invoice − Deposit Paid.
        Positive = buyer still owes, negative = overpaid.

        Before the carrier records the actual purchase the Actual column is
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
    def deposit_pending(self) -> bool:
        """A deposit proof has been submitted but not yet verified by admin."""
        if not hasattr(self, "transaction"):
            return False
        return self.transaction.payments.filter(
            direction=Payment.Direction.INBOUND,
            kind=Payment.Kind.DEPOSIT,
            status=Payment.PaymentStatus.PENDING,
        ).exists()

    @property
    def deposit_proof_url(self) -> str:
        """URL of the most recently uploaded deposit proof, if any."""
        if not hasattr(self, "transaction"):
            return ""
        p = (
            self.transaction.payments.filter(
                direction=Payment.Direction.INBOUND,
                kind=Payment.Kind.DEPOSIT,
            )
            .exclude(proof="")
            .order_by("-id")
            .first()
        )
        return p.proof.url if p and p.proof else ""

    @property
    def balance_paid_amount(self) -> Decimal:
        """Verified non-deposit (balance / top-up) payments."""
        return self._paid_of_kind(Payment.Kind.BALANCE)

    @property
    def balance_pending(self) -> bool:
        """A balance proof has been submitted but not yet verified by admin."""
        if not hasattr(self, "transaction"):
            return False
        return self.transaction.payments.filter(
            direction=Payment.Direction.INBOUND,
            kind=Payment.Kind.BALANCE,
            status=Payment.PaymentStatus.PENDING,
        ).exists()

    @property
    def balance_proof_url(self) -> str:
        """URL of the most recently uploaded balance proof, if any."""
        if not hasattr(self, "transaction"):
            return ""
        p = (
            self.transaction.payments.filter(
                direction=Payment.Direction.INBOUND,
                kind=Payment.Kind.BALANCE,
            )
            .exclude(proof="")
            .order_by("-id")
            .first()
        )
        return p.proof.url if p and p.proof else ""

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
    """A carrier's response to a buyer-first BuyRequest (order). Once selected
    by the buyer it becomes an independent "leg" — its own deposit, drop-off,
    and lifecycle — so a single order can be split across several carriers
    (partial fulfillment) without the legs blocking each other.
    """

    order = models.ForeignKey(BuyRequest, on_delete=models.CASCADE, related_name="traveler_offers")
    traveler = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="traveler_offers")

    ask_cost_per_kg = models.DecimalField(max_digits=12, decimal_places=2)
    avail_kg = models.DecimalField(
        max_digits=6, decimal_places=2, help_text="Carrier's declared spare capacity — shown publicly."
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
        help_text="Carrier's final measured weight at WEIGHT_VERIFIED — always authoritative.",
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

    # Customs duty the traveler paid at the destination for THIS leg, recorded
    # at the arrival step. Reimbursable by the buyer (folded into the balance).
    custom_fare_currency = models.CharField(max_length=3, choices=Currency.choices, blank=True, default="")
    custom_fare_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    custom_fare_proof = models.ImageField(upload_to="leg_custom_fare/", blank=True, null=True, storage=webp_storage)

    # Buyer's bank details for receiving a refund on this leg, free text.
    refund_bank_details = models.TextField(blank=True)

    dropped_off_at = models.DateTimeField(null=True, blank=True)
    weight_verified_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True, help_text="When the buyer marked this leg Clear.")
    # Carrier payout actually released by admin (set from the admin "mark paid"
    # action, not the workflow) so the traveler's payout status reflects real money.
    payout_paid_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["order", "offer_status"])]
        # DB table / model name stay TravelerOffer; only the admin-facing label
        # follows the Traveler→Carrier rename.
        verbose_name = "Carrier offer"
        verbose_name_plural = "Carrier offers"

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
    def travel_date_passed(self) -> bool:
        """True once the travel date has arrived — the proxy can now process
        arrival (records customs duty, marks the package arrived)."""
        return bool(self.travel_date and timezone.now().date() >= self.travel_date)

    @property
    def can_edit(self) -> bool:
        """Carrier may edit/withdraw their offer only while it's still pending AND
        the order is still accepting offers, and not within 24h of the deadline.
        Flow-1 proxy offers stay PENDING through the whole carry flow (no leg is
        selected), so the order-status check is what stops Edit/Cancel showing
        once the buyer has accepted."""
        return (self.offer_status == OfferStatus.PENDING
                and self.order.status in OPEN_ORDER_STATUSES
                and not self.order.listing_locked)

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
    def customs_allocations(self) -> list:
        """The goods this carrier carries (cargo partial fulfilment) — only
        items the buyer assigned to this leg, for the per-leg customs invoice."""
        return [a for a in self.item_allocations.all() if a.quantity > 0]

    @property
    def customs_total(self) -> Decimal:
        """Declared value of the goods this carrier carries."""
        return sum((a.line_total for a in self.customs_allocations), Decimal("0")).quantize(TWO_PLACES)

    @property
    def carried_weight_kg(self) -> Decimal:
        """Final weighed weight if recorded, else the allocated (booked) weight."""
        return self.agreed_weight_kg if self.agreed_weight_kg is not None else (self.allocated_weight_kg or Decimal("0"))

    @property
    def destination_currency(self) -> str:
        """Currency at this leg's destination country — customs duty is paid here.
        Resolved via the fx/kurs table; falls back to IDR."""
        return ExchangeRate.currency_for_country(self.to_country)

    @property
    def deposit_pending(self) -> bool:
        """A deposit proof has been submitted but not yet verified by admin."""
        if self.deposit_verified or not hasattr(self, "transaction"):
            return False
        return self.transaction.payments.filter(
            direction=LegPayment.Direction.INBOUND,
            kind=LegPayment.Kind.DEPOSIT,
            status=LegPayment.PaymentStatus.PENDING,
        ).exists()

    @property
    def deposit_proof_url(self) -> str:
        """URL of the most recently uploaded deposit proof, if any."""
        if not hasattr(self, "transaction"):
            return ""
        p = (
            self.transaction.payments.filter(
                direction=LegPayment.Direction.INBOUND,
                kind=LegPayment.Kind.DEPOSIT,
            )
            .exclude(proof="")
            .order_by("-id")
            .first()
        )
        return p.proof.url if p and p.proof else ""

    @property
    def address_revealed(self) -> bool:
        """Carrier's name and drop-off address stay hidden until the deposit clears."""
        return self.deposit_verified

    @property
    def pickup_address_revealed(self) -> bool:
        """Carrier's destination address stays hidden until Pickup is chosen."""
        return self.fulfillment_method == FulfillmentMethod.PICKUP

    @property
    def weight_delta(self) -> Decimal:
        """(final measured weight − allocated) × accepted ask rate. Positive =
        buyer owes a balance; negative = buyer overpaid (refund due)."""
        final = self.agreed_weight_kg if self.agreed_weight_kg is not None else (self.allocated_weight_kg or Decimal("0"))
        delta_kg = final - (self.allocated_weight_kg or Decimal("0"))
        return (delta_kg * self.ask_cost_per_kg).quantize(TWO_PLACES)

    @property
    def custom_fare_in_order_currency(self) -> Decimal:
        """Customs duty converted to the order's currency (BCA sell_rate = IDR
        per 1 unit of foreign currency). Mirrors BuyRequest.actual_custom."""
        if not self.custom_fare_amount:
            return Decimal("0.00")
        inv = self.order.currency
        fare_ccy = self.custom_fare_currency or inv
        if fare_ccy == inv:
            return self.custom_fare_amount.quantize(TWO_PLACES)
        if fare_ccy == "IDR" and inv != "IDR":
            try:
                rate = ExchangeRate.objects.get(code=inv, is_active=True)
                if rate.sell_rate:
                    return (self.custom_fare_amount / rate.sell_rate).quantize(TWO_PLACES)
            except ExchangeRate.DoesNotExist:
                pass
        elif inv == "IDR" and fare_ccy != "IDR":
            try:
                rate = ExchangeRate.objects.get(code=fare_ccy, is_active=True)
                if rate.sell_rate:
                    return (self.custom_fare_amount * rate.sell_rate).quantize(TWO_PLACES)
            except ExchangeRate.DoesNotExist:
                pass
        return self.custom_fare_amount.quantize(TWO_PLACES)

    @property
    def custom_fare_needs_conversion(self) -> bool:
        return bool(
            self.custom_fare_amount
            and self.custom_fare_currency
            and self.custom_fare_currency != self.order.currency
        )

    @property
    def custom_fare_proof_url(self) -> str:
        return self.custom_fare_proof.url if self.custom_fare_proof else ""

    @property
    def settlement_net(self) -> Decimal:
        """What the buyer still owes the carrier at arrival: the weight-delta
        plus the reimbursable customs duty. Negative = refund to the buyer."""
        return (self.weight_delta + self.custom_fare_in_order_currency).quantize(TWO_PLACES)

    @property
    def extra_due(self) -> Decimal:
        """Amount the buyer still owes (heavier weight and/or customs duty)."""
        n = self.settlement_net
        return n if n > 0 else Decimal("0.00")

    @property
    def refund_due(self) -> Decimal:
        """Amount to refund the buyer (lighter weight outweighs any duty)."""
        n = self.settlement_net
        return -n if n < 0 else Decimal("0.00")

    def _idr_equivalent(self, amount: Decimal) -> "Decimal | None":
        """Convert an order-currency amount to IDR via the BCA sell rate, or
        None when the order is already in IDR / no active rate exists."""
        inv = self.order.currency
        if inv == "IDR":
            return None
        try:
            rate = ExchangeRate.objects.get(code=inv, is_active=True)
            if not rate.sell_rate:
                return None
            return (amount * rate.sell_rate).quantize(TWO_PLACES)
        except ExchangeRate.DoesNotExist:
            return None

    @property
    def deposit_due_idr(self) -> "Decimal | None":
        return self._idr_equivalent(self.deposit_due)

    @property
    def extra_due_idr(self) -> "Decimal | None":
        return self._idr_equivalent(self.extra_due)

    @property
    def payout_ready(self) -> bool:
        """The leg has arrived, so the carrier payout figures are final."""
        return self.leg_status in {
            LegStatus.PACKAGE_ARRIVED, LegStatus.READY_FOR_PICKUP,
            LegStatus.RESHIP_REQUESTED, LegStatus.RESHIP_COST_SENT,
            LegStatus.RESHIPPING, LegStatus.CLEAR, LegStatus.CLOSED,
        }

    @property
    def payout_disbursable(self) -> bool:
        """The leg cleared (buyer confirmed pickup/receipt), so the carrier
        payout is eligible for the admin to release — not necessarily paid yet."""
        return self.leg_status in {LegStatus.CLEAR, LegStatus.CLOSED}

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
    def balance_pending(self) -> bool:
        """A balance proof has been submitted but not yet verified by admin."""
        if self.balance_settled or not hasattr(self, "transaction"):
            return False
        return self.transaction.payments.filter(
            direction=LegPayment.Direction.INBOUND,
            kind=LegPayment.Kind.BALANCE,
            status=LegPayment.PaymentStatus.PENDING,
        ).exists()

    @property
    def balance_proof_url(self) -> str:
        """URL of the most recently uploaded balance proof, if any."""
        if not hasattr(self, "transaction"):
            return ""
        p = (
            self.transaction.payments.filter(
                direction=LegPayment.Direction.INBOUND,
                kind=LegPayment.Kind.BALANCE,
            )
            .exclude(proof="")
            .order_by("-id")
            .first()
        )
        return p.proof.url if p and p.proof else ""

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
    fulfilled order has one deposit/payout per carrier, not one for the
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
        """Total amount earned by the carrier for this leg, based on final weight."""
        leg = self.leg
        weight = leg.agreed_weight_kg if leg.agreed_weight_kg is not None else leg.allocated_weight_kg
        return ((weight or Decimal("0")) * leg.ask_cost_per_kg).quantize(TWO_PLACES)

    @property
    def commission_amount(self) -> Decimal:
        amount = self.gross_amount * self.commission_percent / Decimal("100")
        return max(amount, platform_fee_floor(self.currency)).quantize(TWO_PLACES)

    @property
    def payout_to_traveler(self) -> Decimal:
        # Customs duty is reimbursed in full (no commission charged on it).
        return (
            self.gross_amount - self.commission_amount + self.leg.custom_fare_in_order_currency
        ).quantize(TWO_PLACES)

    @property
    def custom_fare(self) -> Decimal:
        return self.leg.custom_fare_in_order_currency

    @property
    def subtotal_before_fee(self) -> Decimal:
        """Carrying fee (final weight × ask) + reimbursable customs duty."""
        return (self.gross_amount + self.custom_fare).quantize(TWO_PLACES)

    @property
    def payout_to_traveler_idr(self) -> "Decimal | None":
        return self.leg._idr_equivalent(self.payout_to_traveler)


class LegPayment(models.Model):
    """A single money movement against a leg's deposit. Mirrors `Payment`'s
    shape (same kinds of choices) but kept independent of it."""

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound (to admin)"
        OUTBOUND = "outbound", "Outbound (from admin)"

    class Kind(models.TextChoices):
        DEPOSIT = "deposit", "Carrier deposit"
        BALANCE = "balance", "Weight-delta balance"
        PAYOUT = "payout", "Payout to carrier"
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
    """One requested item; the carrier fills in cost + actual-purchase fields."""

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

    @property
    def allocated_quantity(self) -> int:
        """Units already assigned to a carrier (cargo partial fulfilment)."""
        return sum((a.quantity for a in self.leg_allocations.all()), 0)

    @property
    def unallocated_quantity(self) -> int:
        return max(self.quantity - self.allocated_quantity, 0)


class ItemLegAllocation(models.Model):
    """How many units of a declared cargo item a specific carrier (leg) carries.
    In partial fulfilment the goods are split across carriers who travel on
    different flights, so each clears customs with only their share — this drives
    the per-leg customs invoice."""

    item = models.ForeignKey(RequestItem, on_delete=models.CASCADE, related_name="leg_allocations")
    leg = models.ForeignKey("TravelerOffer", on_delete=models.CASCADE, related_name="item_allocations")
    quantity = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = ("item", "leg")

    def __str__(self):
        return f"{self.item.name} ×{self.quantity} → leg {self.leg_id}"

    @property
    def unit_cost(self) -> Decimal:
        return self.item.actual_unit_cost or self.item.estimated_unit_cost or Decimal("0")

    @property
    def line_total(self) -> Decimal:
        return self.unit_cost * self.quantity


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
        """Platform fee: 2.5%, deducted once at closing. Cargo (Carrier) charges
        it on the carry fee only — the reimbursed customs duty is a pass-through.
        For Flow-1 proxy orders the carrier only carries, so their fee is on the
        carry fee too (the proxy's product+margin fee is withheld separately)."""
        base = (self.request.effective_shipment_cost
                if self.request.carrier_charges_carry_only else self.request.invoice_total)
        amount = base * self.commission_percent / Decimal("100")
        return max(amount, platform_fee_floor(self.currency)).quantize(TWO_PLACES)

    @property
    def payout_to_traveler(self) -> Decimal:
        """Paid to the carrier when the transaction CLOSES (both parties
        cleared), minus the 2.5% platform fee. Cargo (Carrier): carry fee +
        reimbursed customs, fee on the carry fee only. Proxy buying: the full
        invoice (items + margin + shipment + custom fare). The deposit is only
        held by admin meanwhile — there is no payout before closing. Flow-1 proxy
        orders pay the carrier the carry fee + reimbursed customs only; the
        product+margin is disbursed to the proxy buyer separately.
        """
        gross = (self.request.cargo_charge_total
                 if self.request.carrier_charges_carry_only else self.request.invoice_total)
        return (gross - self.commission_amount).quantize(TWO_PLACES)

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
        PAYOUT = "payout", "Payout to carrier"
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
    sell_rate = models.DecimalField(max_digits=14, decimal_places=4, verbose_name="FX Rate")
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

    @classmethod
    def country_currency_map(cls) -> dict:
        """Same mapping as `currency_for_country`, exposed as a lowercased
        country-name -> currency-code dict for client-side lookups (e.g. live
        currency hints while a form is being filled in, before submission)."""
        mapping = {}
        for rate in cls.objects.filter(is_active=True).exclude(apply_to_countries=""):
            for name in rate.apply_to_countries.split(","):
                name = name.strip().lower()
                if name:
                    mapping[name] = rate.code
        return mapping


class ProxyBuyer(models.Model):
    """An admin-curated proxy buyer who sources & purchases products in an origin
    country (they do NOT travel — carriage is a separate carrier). A fixed list,
    approved by admin, no self-signup/verification. One per country for launch,
    but the schema allows several per country later (no DB uniqueness on country).
    The Product Buyer picks one of these to start a Flow-1 (proxy buying) order."""

    name = models.CharField(max_length=120)
    country = models.CharField(max_length=80, choices=COUNTRY_CHOICES)
    city = models.CharField(max_length=80, blank=True)
    margin_range = models.CharField(
        max_length=20, blank=True,
        help_text="Indicative margin shown publicly, e.g. \"15-25%\". Free text — "
                  "the actual margin is set per order on the proxy's estimate.",
    )
    email = models.EmailField()
    whatsapp = models.CharField(max_length=32, blank=True)
    # Optional link to a real Buyer account (logins as a Buyer); curated entries
    # may exist before/without an account.
    user = models.ForeignKey(
        USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="proxy_buyer_profiles"
    )
    is_active = models.BooleanField(default=True)
    sequence = models.PositiveSmallIntegerField(default=0, help_text="Lower = shown first.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence", "country", "name"]
        verbose_name = "Proxy Buyer"
        verbose_name_plural = "Proxy Buyers"

    def __str__(self):
        return f"{self.name} — {self.country}"

    @property
    def whatsapp_link(self):
        """wa.me link from the stored number (digits only)."""
        digits = "".join(ch for ch in self.whatsapp if ch.isdigit())
        return f"https://wa.me/{digits}" if digits else ""


class Message(models.Model):
    """A chat message between the buyer and carrier on a request (admin can
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
        if request_obj.proxy_buyer_id and self.sender_id == request_obj.proxy_buyer.user_id:
            return "Proxy Buyer"
        traveler = request_obj.traveler_user
        if traveler and self.sender_id == traveler.id:
            return "Carrier"
        return "Admin"
