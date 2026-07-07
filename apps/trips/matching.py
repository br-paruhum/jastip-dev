"""Carrier-First matching — surfacing queued carriers to RESPONDED Products orders,
plus the hold / accept / reject / expire mechanics.

See PLAN-carrier-first-orders.md. The order stays on buyer-first Products rails the
whole way (``order.plan`` is never set); a CarrierMatch is a thin surfacing + hold
record against a queued ``TravelPlan``. On accept we spin up a PENDING
``TravelerOffer`` from the plan and route it through the existing ``order_accept``
path, so the entire Flow-1 downstream is reused unchanged.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction as db_transaction
from django.utils import timezone

from . import workflow
from .constants import (
    HELD_MATCH_STATUSES,
    OPEN_PLAN_STATUSES,
    MatchSource,
    MatchStatus,
    OfferStatus,
    Status,
)
from .models import CarrierMatch, Order, TravelerOffer, TravelPlan


def _settings():
    from apps.pages.models import SiteSettings
    return SiteSettings.load()


def order_is_matchable(order) -> bool:
    """Carrier-First applies to Products buyer-first orders that have cleared the
    proxy estimate and are 'Looking for a Carrier' (status RESPONDED)."""
    return (
        order.plan_id is None
        and not order.is_cargo
        and order.proxy_buyer_id is not None
        and order.status == Status.RESPONDED
        and order.estimated_weight_kg > 0
    )


def find_matching_plans(order):
    """Open queued TravelPlans on the order's route that can reach the buyer in
    time (PLAN §7 matching gate). The per-plan capacity guard-rail is applied by
    the caller — it needs the CarrierMatch ledger."""
    if not order_is_matchable(order):
        return TravelPlan.objects.none()
    min_travel = timezone.now().date() + timedelta(days=_settings().carrier_match_lead_days)
    qs = (
        TravelPlan.objects
        .filter(
            status__in=OPEN_PLAN_STATUSES,
            from_city__iexact=order.from_city, from_country__iexact=order.from_country,
            to_city__iexact=order.to_city, to_country__iexact=order.to_country,
            travel_date__gte=min_travel,
        )
        .exclude(traveler_id=order.buyer_id)
        .prefetch_related("buy_requests", "carrier_matches")
    )
    # Deadline gate. OPEN QUESTION (default resolved): `<=` — a carrier arriving ON
    # the buyer's deadline still qualifies. Flip to `travel_date__lt` for strict.
    # Skipped when the order carries no deadline.
    if order.max_acceptable_date:
        qs = qs.filter(travel_date__lte=order.max_acceptable_date)
    return qs


def surface_matches(order, *, source=MatchSource.PUSH, notify=True):
    """Create PENDING CarrierMatch holds for every qualifying plan not already
    surfaced to this order, respecting the per-plan capacity guard-rail (PLAN §5).
    Returns the list of created matches."""
    weight = order.estimated_weight_kg
    if weight <= 0:
        return []
    cfg = _settings()
    now = timezone.now()
    expires = now + timedelta(hours=cfg.carrier_match_window_hours)
    already = set(
        order.carrier_matches.filter(status__in=HELD_MATCH_STATUSES)
        .values_list("plan_id", flat=True)
    )
    created = []
    for plan in find_matching_plans(order):
        if plan.id in already:
            continue
        if plan.carrier_first_remaining_kg < weight:  # guard-rail — no oversell
            continue
        created.append(CarrierMatch.objects.create(
            order=order, plan=plan, allocated_kg=weight, source=source,
            offered_at=now, window_expires_at=expires,
        ))
    if created and notify:
        workflow.on_carrier_matches_surfaced(order, created)
    return created


def send_order_to_plan(order, plan, *, by_user):
    """Pull path (board 'Send Order', PLAN §6.d): the buyer puts their order in
    front of a specific queued carrier. Creates a PENDING hold they then accept
    from their order page. Returns (match_or_None, message)."""
    if by_user != order.buyer:
        return None, "Only the buyer can send this order."
    if not order_is_matchable(order):
        return None, "This order isn't ready to be sent to a carrier yet."
    if plan.traveler_id == order.buyer_id:
        return None, "You can't send your order to your own travel plan."
    if plan.status not in OPEN_PLAN_STATUSES:
        return None, "This carrier is no longer accepting orders."
    weight = order.estimated_weight_kg
    if plan.carrier_first_remaining_kg < weight:
        return None, "This carrier no longer has enough spare weight for your order."
    if order.carrier_matches.filter(plan=plan, status__in=HELD_MATCH_STATUSES).exists():
        return None, "You've already sent this order to that carrier."
    cfg = _settings()
    now = timezone.now()
    match = CarrierMatch.objects.create(
        order=order, plan=plan, allocated_kg=weight, source=MatchSource.PULL,
        offered_at=now, window_expires_at=now + timedelta(hours=cfg.carrier_match_window_hours),
    )
    return match, "Order sent — review and accept the carrier's rate on your order page."


def accept_match(match, *, by_user):
    """Buyer accepts a surfaced carrier. Spins up a PENDING TravelerOffer from the
    plan and routes it through the existing order_accept path (order → ACCEPTED,
    proxy + carrier notified). Marks this match accepted and releases sibling holds.
    Returns (ok, message)."""
    order = match.order
    if by_user != order.buyer:
        return False, "Only the buyer can accept this carrier."
    if not match.is_live:
        return False, "This carrier offer is no longer available."
    if order.status != Status.RESPONDED:
        return False, "This order is no longer awaiting a carrier."
    plan = match.plan
    now = timezone.now()
    with db_transaction.atomic():
        offer = TravelerOffer.objects.create(
            order=order,
            traveler=plan.traveler,
            ask_cost_per_kg=plan.shipment_cost_per_kg,
            avail_kg=match.allocated_kg,
            travel_date=plan.travel_date,
            travel_time=plan.travel_time,
            from_city=plan.from_city, from_country=plan.from_country,
            to_city=plan.to_city, to_country=plan.to_country,
            pickup_address=getattr(plan.traveler, "traveler_address", "") or "",
            offer_status=OfferStatus.PENDING,
        )
        # Mirror order_accept: order → ACCEPTED, decline any competing pending offers.
        order.status = Status.ACCEPTED
        order.save(update_fields=["status", "updated_at"])
        order.traveler_offers.filter(offer_status=OfferStatus.PENDING).exclude(
            pk=offer.pk
        ).update(offer_status=OfferStatus.REJECTED)
        match.status = MatchStatus.ACCEPTED
        match.offer = offer
        match.responded_at = now
        match.save(update_fields=["status", "offer", "responded_at", "updated_at"])
        # Release every other live hold this order held so their carriers free up.
        order.carrier_matches.filter(status=MatchStatus.PENDING).exclude(
            pk=match.pk
        ).update(status=MatchStatus.REJECTED, responded_at=now)
    workflow.on_proxy_offer_accepted(order, offer)
    return True, "Carrier accepted. Please pay the deposit to confirm."


def reject_match(match, *, by_user):
    """Buyer dismisses one surfaced carrier (releases its hold; the carrier stays
    on the Queuing board for other buyers)."""
    order = match.order
    if by_user != order.buyer:
        return False, "Only the buyer can dismiss this carrier."
    if match.status != MatchStatus.PENDING:
        return False, "This carrier offer is no longer pending."
    match.status = MatchStatus.REJECTED
    match.responded_at = timezone.now()
    match.save(update_fields=["status", "responded_at", "updated_at"])
    return True, "Carrier dismissed."


def expire_stale_matches():
    """Flip pending matches past their accept window to expired — releasing their
    held weight (the ledger sums live rows only). Returns the number expired."""
    now = timezone.now()
    return (
        CarrierMatch.objects
        .filter(status=MatchStatus.PENDING, window_expires_at__lt=now)
        .update(status=MatchStatus.EXPIRED, responded_at=now)
    )


def run_matcher(*, notify=True):
    """Surface matches for every currently-matchable order. Returns total created."""
    total = 0
    orders = (
        Order.objects
        .filter(
            plan__isnull=True, cargo_only=False, status=Status.RESPONDED,
            proxy_buyer__isnull=False, estimated_weight_kg__gt=0,
        )
        .prefetch_related("carrier_matches")
    )
    for order in orders:
        total += len(surface_matches(order, notify=notify))
    return total
