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
    expires = now + timedelta(minutes=cfg.carrier_match_window_minutes)
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


def plan_fits_order(plan, order) -> bool:
    """Whether a specific queued plan satisfies the matching gate (§7) for a
    specific order: same route, enough lead time, within the buyer's deadline,
    not the buyer's own plan, and still open."""
    if plan.status not in OPEN_PLAN_STATUSES or plan.traveler_id == order.buyer_id:
        return False
    same_route = (
        plan.from_city.lower() == order.from_city.lower()
        and plan.from_country.lower() == order.from_country.lower()
        and plan.to_city.lower() == order.to_city.lower()
        and plan.to_country.lower() == order.to_country.lower()
    )
    if not same_route:
        return False
    min_travel = timezone.now().date() + timedelta(days=_settings().carrier_match_lead_days)
    if plan.travel_date < min_travel:
        return False
    # Deadline gate: `<=` (arrive-on-deadline OK — the deadline is the buyer's
    # expectation, not a hard cutoff). Skipped when no deadline is set.
    if order.max_acceptable_date and plan.travel_date > order.max_acceptable_date:
        return False
    return True


def send_carrier_to_buyer(plan, *, buyer):
    """Pull path (home board 'Send Order', PLAN §6.d): the buyer picked a queued
    carrier. Put that carrier in front of each of the buyer's orders that are
    'Looking for a Carrier' and fit this plan (route / timing / capacity) by
    creating a PENDING hold on each — so the carrier shows up (with Accept) when
    they open the order on the My Orders page. Returns (created_count, message)."""
    if plan.traveler_id == buyer.id:
        return 0, "You can't send an order to your own travel plan."
    if plan.status not in OPEN_PLAN_STATUSES:
        return 0, "This carrier is no longer accepting orders."
    orders = [
        o for o in Order.objects.filter(
            buyer=buyer, plan__isnull=True, cargo_only=False,
            status=Status.RESPONDED, proxy_buyer__isnull=False,
        ).prefetch_related("carrier_matches")
        if order_is_matchable(o)
    ]
    if not orders:
        return 0, ("You have no order ready for a carrier yet — accept your "
                   "proxy buyer's estimate first.")
    now = timezone.now()
    expires = now + timedelta(minutes=_settings().carrier_match_window_minutes)
    # Track remaining locally so we never oversell this one plan across several of
    # the buyer's orders in a single click (the prefetch cache won't refresh mid-loop).
    remaining = plan.carrier_first_remaining_kg
    created = 0
    for order in orders:
        weight = order.estimated_weight_kg
        if not plan_fits_order(plan, order) or remaining < weight:
            continue
        if order.carrier_matches.filter(plan=plan, status__in=HELD_MATCH_STATUSES).exists():
            continue
        CarrierMatch.objects.create(
            order=order, plan=plan, allocated_kg=weight, source=MatchSource.PULL,
            offered_at=now, window_expires_at=expires,
        )
        remaining -= weight
        created += 1
    if created == 0:
        return 0, "This carrier doesn't fit any of your open orders (route, dates or capacity)."
    return created, (
        f"Carrier sent to {created} order{'s' if created != 1 else ''}. "
        "Open an order below and Accept the carrier."
    )


def hold_for_estimate(order):
    """Flow-2 (Carrier-First): the Proxy Buyer sent or edited the estimate on a
    carrier-bound order. Place — or refresh, on re-estimate — the weight hold on
    the bound plan and (re)start the accept window (``carrier_match_window_minutes``).

    The hold = the Proxy's estimate weight; it subtracts from the plan's
    ``carrier_first_remaining_kg`` until the buyer accepts (hold becomes committed)
    or the window lapses (``expire_stale_matches`` flips it to EXPIRED, freeing the
    weight — the binding on ``order.carrier_first_plan`` is kept regardless).
    Returns the CarrierMatch, or None if the order is not carrier-bound / weightless."""
    plan = order.carrier_first_plan
    if plan is None:
        return None
    weight = order.estimated_weight_kg
    if not weight or weight <= 0:
        return None
    now = timezone.now()
    expires = now + timedelta(minutes=_settings().carrier_match_window_minutes)
    # Reuse a still-pending hold (re-estimate) or start a fresh one; any earlier
    # EXPIRED/REJECTED rows are left as history and don't count against capacity.
    match = order.carrier_matches.filter(plan=plan, status=MatchStatus.PENDING).first()
    if match is None:
        match = CarrierMatch(order=order, plan=plan, source=MatchSource.PULL)
    match.allocated_kg = weight
    match.status = MatchStatus.PENDING
    match.offered_at = now
    match.window_expires_at = expires
    match.responded_at = None
    match.save()
    return match


def accept_bound_carrier(order, *, by_user):
    """Flow-2 (Carrier-First): the buyer accepted the proxy estimate on an order
    that is already bound to a carrier (``carrier_first_plan``). Re-check the
    carrier still has room, then spin up a PENDING TravelerOffer from the plan and
    advance the order straight to ACCEPTED (deposit) — skipping the RESPONDED
    'looking for a carrier' step (the carrier was chosen at order time).
    Returns (ok, message)."""
    if by_user != order.buyer:
        return False, "Only the buyer can accept the estimate."
    plan = order.carrier_first_plan
    if plan is None:
        return False, "This order is not bound to a carrier."
    if order.status != Status.ESTIMATE_SENT:
        return False, "There is no estimate to accept at this stage."
    weight = order.estimated_weight_kg
    if not weight or weight <= 0:
        return False, "The proxy estimate has no weight yet."
    now = timezone.now()
    # Capacity re-check (Flow-2 decision 3): ``carrier_first_remaining_kg`` already
    # subtracts this order's own live hold, so add it back to measure the true room
    # for THIS order. After a timed-out hold the room may have been taken by others.
    own_hold = sum(
        (m.allocated_kg for m in order.carrier_matches.all()
         if m.status == MatchStatus.PENDING and m.window_expires_at > now),
        0,
    )
    if plan.carrier_first_remaining_kg + own_hold < weight:
        return False, "This carrier no longer has enough spare weight for your order."
    with db_transaction.atomic():
        offer = TravelerOffer.objects.create(
            order=order,
            traveler=plan.traveler,
            ask_cost_per_kg=plan.shipment_cost_per_kg,
            avail_kg=weight,
            travel_date=plan.travel_date,
            travel_time=plan.travel_time,
            from_city=plan.from_city, from_country=plan.from_country,
            to_city=plan.to_city, to_country=plan.to_country,
            pickup_address=getattr(plan.traveler, "traveler_address", "") or "",
            offer_status=OfferStatus.PENDING,
        )
        order.status = Status.ACCEPTED
        order.save(update_fields=["status", "updated_at"])
        order.traveler_offers.filter(offer_status=OfferStatus.PENDING).exclude(
            pk=offer.pk
        ).update(offer_status=OfferStatus.REJECTED)
        # Commit the hold: reuse the pending/expired hold row (or start one) as the
        # ACCEPTED capacity record so the weight stays reserved through the deposit.
        match = (
            order.carrier_matches.filter(plan=plan).exclude(status=MatchStatus.ACCEPTED)
            .order_by("-created_at").first()
        )
        if match is None:
            match = CarrierMatch(
                order=order, plan=plan, source=MatchSource.PULL,
                offered_at=now, window_expires_at=now,
            )
        match.allocated_kg = weight
        match.status = MatchStatus.ACCEPTED
        match.offer = offer
        match.responded_at = now
        match.save()
    workflow.on_proxy_offer_accepted(order, offer)
    return True, "Estimate accepted. Please pay the deposit to confirm."


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
