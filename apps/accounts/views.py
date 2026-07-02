import json

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.notifications.services import send_whatsapp
from apps.trips.constants import BUYER_FIRST_TERMINAL_STATUSES, OPEN_ORDER_STATUSES, REFUND_ELIGIBLE_STATUSES, STATUS_TONE, LegStatus, OfferStatus, Status
from apps.trips.forms import (
    AWBForm, BuyRequestForm, CustomFareForm, LegCustomFareForm, OrderForm,
    OrderItemFormSet, PurchaseItemFormSet, PurchaseWeightForm, ReshipmentCostForm,
    ProxyEstimateForm, RequestItemFormSet, ReviewForm, ReviewItemFormSet,
    TravelPlanForm, TravelerCargoOfferForm, TravelerOfferForm,
)
from apps.trips.models import Order, ExchangeRate, TravelerOffer, TravelPlan

from .forms import ChangePasswordForm, OTPForm, ProfileForm


def _profile_tab(tab):
    """Redirect back to the profile page with a given sidebar tab active."""
    return redirect(reverse("accounts:profile") + f"#{tab}")


def _resolve_role(request):
    """Current Carrier/Buyer choice for this session, self-healing into the
    session from the user's last choice if not set yet (e.g. a session that
    predates this feature, or one that was never routed through choose_role)."""
    role = request.session.get("role")
    if role not in ("traveler", "buyer"):
        role = request.user.last_role_choice or "traveler"
        request.session["role"] = role
    return role


def _proxy_buying_orders(user):
    """Flow-1 orders the user (acting as a Proxy Buyer) has been assigned to
    source. Empty for a regular buyer who has never been an assigned proxy —
    drives whether the "My Proxy Buying" sidebar item shows."""
    return list(
        Order.objects.filter(proxy_buyer__user=user, plan__isnull=True)
        .exclude(status__in=BUYER_FIRST_TERMINAL_STATUSES)
        .select_related("buyer", "proxy_buyer")
        .order_by("max_acceptable_date")
    )


def _pending_disbursements(user):
    """Superuser-only roll-up: every cleared order with a proxy or carrier
    disbursement that is eligible to release but not yet paid. Drives the
    'Pending Disbursement' dashboard tab so staff release them from one place."""
    if not getattr(user, "is_superuser", False):
        return []
    orders = (
        Order.objects
        .filter(status__in=[Status.CLEAR, Status.CLOSED])
        .select_related("buyer", "proxy_buyer__user", "plan__traveler")
        .order_by("-updated_at")
    )
    rows = []
    for o in orders:
        pending = []
        if o.proxy_disbursable and not o.proxy_disbursed_at:
            pending.append({
                "kind": "proxy", "label": "Proxy Buyer",
                "recipient": o.proxy_buyer.name if o.proxy_buyer_id else "—",
                "amount": o.proxy_disbursement_total,
            })
        if o.traveler_payout_disbursable and not o.traveler_paid_at:
            try:
                tx = o.transaction
            except Exception:
                tx = None
            t = o.traveler_user
            pending.append({
                "kind": "traveler", "label": "Carrier",
                "recipient": t.full_name if t else "—",
                "amount": tx.payout_to_traveler if tx else None,
            })
        if pending:
            rows.append({"order": o, "pending": pending})

    # Buyer overpaid refunds. Because the buyer pays 100% upfront, an overpaid
    # order auto-settles past Package Arrived straight to Paid in Full, so the
    # refund is owed across the whole arrival-onward range — scan all of it (the
    # CLEAR/CLOSED scan above only emits payout rows, never refunds).
    refunds = (
        Order.objects
        .filter(status__in=REFUND_ELIGIBLE_STATUSES, refund_processed=False)
        .select_related("buyer", "proxy_buyer__user", "plan__traveler")
        .order_by("-updated_at")
    )
    for o in refunds:
        if o.refund_due <= 0:
            continue
        rows.append({"order": o, "pending": [{
            "kind": "refund", "label": "Buyer Refund",
            "recipient": o.buyer.full_name if o.buyer_id else "—",
            "amount": o.refund_due,
            "bank_name": o.refund_bank_name,
            "account_no": o.refund_account_no,
            "account_name": o.refund_account_name,
        }]})
    return rows


def _order_is_proxy(req, user):
    """True if `user` is the carrier/proxy for `req`: the plan's carrier
    (plan-first) or the single live offer's carrier (buyer-first Products)."""
    if req.plan_id:
        return req.plan.traveler_id == user.id
    live = [o for o in req.traveler_offers.all()
            if o.offer_status in (OfferStatus.PENDING, OfferStatus.SELECTED)]
    return len(live) == 1 and live[0].traveler_id == user.id


def _assignment_rows(order):
    """Grid for the buyer's 'Assign goods to carriers' panel: one row per item,
    a cell (leg + current qty) per carrier, plus how many units remain."""
    legs = order.confirmed_legs
    rows = []
    for item in order.items.all():
        amap = {a.leg_id: a.quantity for a in item.leg_allocations.all()}
        rows.append({
            "item": item,
            "cells": [{"leg": leg, "qty": amap.get(leg.id, 0)} for leg in legs],
            "unassigned": item.unallocated_quantity,
        })
    return rows


@login_required
def choose_role(request):
    if request.method == "POST":
        role = request.POST.get("role")
        if role not in ("traveler", "buyer"):
            messages.error(request, "Please choose Carrier or Buyer.")
            return redirect("accounts:choose_role")
        request.session["role"] = role
        request.user.last_role_choice = role
        request.user.save(update_fields=["last_role_choice"])
        next_url = request.session.pop("post_role_next", None)
        return redirect(next_url or reverse("accounts:profile"))
    current_role = request.user.last_role_choice or "traveler"
    return render(request, "accounts/choose_role.html", {"current_role": current_role})


def _offer_status_display(offer):
    """Status label + badge tone mirroring the old standalone offer table."""
    if offer.offer_status == OfferStatus.PENDING:
        return "Pending", "muted"
    if offer.offer_status == OfferStatus.SELECTED:
        if offer.leg_status:
            return offer.get_leg_status_display(), STATUS_TONE.get(offer.leg_status, "info")
        return "Awaiting drop-off", "warning"
    return offer.get_offer_status_display(), "muted"


# Carrier "My Travel Plans" shows a simplified trip status: every in-progress
# step just reads "Pending" — only once the buyer clears (or the leg closes /
# is cancelled / rejected) does it switch to the real terminal label.
_TRIP_TERMINAL_LABELS = {
    Status.CLEAR: "Clear",
    Status.CLOSED: "Closed",
    Status.CANCELLED: "Cancelled",
    Status.REJECTED: "Rejected",
}


def _trip_status_label(status) -> str:
    return _TRIP_TERMINAL_LABELS.get(status, "Pending")


def _travel_rows(plans, offers):
    """Combine a carrier's posted plans (one row per request-within-plan,
    mirroring active_requests_with_capacity) with their buyer-first offers
    into one sorted list of row dicts for the merged "My Travel Plans" tab.
    Type is derived from origin (plan vs. offer), not UI navigation state."""
    rows = []
    for plan in plans:
        cap_items = plan.active_requests_with_capacity
        if cap_items:
            for item in cap_items:
                rows.append({
                    "kind": "plan", "bf_kind": "traveler_first",
                    "type_label": plan.type_label, "type_is_cargo": plan.carrier_only,
                    # Hide-closed toggle also folds away cleared orders, not just closed.
                    "is_closed": plan.is_closed or item.req.status in {Status.CLEAR, Status.CLOSED},
                    # Each cargo order on a carrier plan is its own transaction, so
                    # label the row with the order's own REQ ref (what the buyer
                    # sees) — not the plan ref, which made multiple orders on one
                    # plan look like duplicate rows.
                    "ref": item.req.reference, "plan_ref": plan.reference,
                    "date": plan.travel_date, "route": plan.route,
                    "available": item.available, "remaining": item.remaining,
                    "counterparty": item.req.buyer.full_name,
                    "status_label": _trip_status_label(item.req.status), "status_tone": item.req.status_tone,
                    "req": item.req, "plan": plan, "sort_key": item.req.created_at,
                })
        else:
            rows.append({
                "kind": "plan_only", "bf_kind": "traveler_first",
                "type_label": plan.type_label, "type_is_cargo": plan.carrier_only,
                "is_closed": plan.is_closed or plan.status in {Status.CLEAR, Status.CLOSED},
                "ref": plan.reference, "date": plan.travel_date, "route": plan.route,
                "available": plan.available_weight_kg, "remaining": plan.remaining_weight_kg,
                "counterparty": None,
                "status_label": _trip_status_label(plan.status), "status_tone": plan.status_tone,
                "plan": plan, "sort_key": plan.created_at,
            })
    for offer in offers:
        if not offer.order.is_cargo:
            # Proxy (Flow-1) Products carry: the offer never formally progresses
            # (offer_status stays pending), so track the ORDER lifecycle instead.
            if offer.offer_status == OfferStatus.REJECTED:
                label, tone = "Not Selected", "muted"
            elif offer.offer_status == OfferStatus.WITHDRAWN:
                label, tone = "Withdrawn", "muted"
            elif offer.order.status in {Status.CLEAR, Status.CLOSED}:
                label, tone = "Received", "info"
            elif offer.order.status == Status.RESPONDED:
                # Offer sent; the buyer hasn't picked a carrier yet.
                label, tone = "Waiting for Buyer's Acceptance", "info"
            else:
                label, tone = "Pending", "info"
        else:
            label, tone = _offer_status_display(offer)
        offer_closed = (
            offer.offer_status in {OfferStatus.REJECTED, OfferStatus.WITHDRAWN}
            or offer.leg_status in {LegStatus.CLEAR, LegStatus.CLOSED, LegStatus.DROPOFF_MISSED}
            or (not offer.order.is_cargo and offer.order.status in {Status.CLEAR, Status.CLOSED})
        )
        rows.append({
            "kind": "offer", "bf_kind": "buyer_first",
            "type_label": offer.order.counterparty_label, "type_is_cargo": offer.order.is_cargo,
            "is_closed": offer_closed,
            "offer_not_selected": offer.offer_status in {OfferStatus.REJECTED, OfferStatus.WITHDRAWN},
            "ref": offer.order.reference, "date": offer.travel_date, "route": offer.route,
            "ask_cost_per_kg": offer.ask_cost_per_kg, "kg": offer.allocated_weight_kg or offer.avail_kg,
            "currency": offer.order.currency,
            "status_label": label, "status_tone": tone,
            "offer": offer, "sort_key": offer.created_at,
        })
    rows.sort(key=lambda r: r["sort_key"], reverse=True)
    return rows


@login_required
def profile(request):
    user = request.user
    role = _resolve_role(request)

    travel_rows = []
    orders_proxy = orders_cargo = []
    proxy_orders = []

    if role == "traveler":
        # Traveler side: travel plans merged with buyer-first offers into one
        # "My Travel Plans" list (the traveler doesn't care about Proxy vs Carrier).
        # Closed rows stay inline (hidden via the dashboard's "hide closed" toggle).
        my_plans = list(TravelPlan.objects.filter(traveler=user).prefetch_related("buy_requests"))
        my_offers = list(TravelerOffer.objects.filter(traveler=user).select_related("order"))
        travel_rows = _travel_rows(my_plans, my_offers)
    else:
        # Buyer side: plan-first requests merged with buyer-first orders, split by
        # transaction type into Proxy Buying vs Carrier Only.
        my_buying = list(Order.objects.filter(buyer=user, plan__isnull=False).select_related("plan"))
        my_bf_orders = list(
            Order.objects.filter(buyer=user, plan__isnull=True).prefetch_related("traveler_offers")
        )
        for r in my_buying:
            r.bf_kind = "traveler_first"
        for o in my_bf_orders:
            o.bf_kind = "buyer_first"
        all_my_orders = sorted(my_buying + my_bf_orders, key=lambda r: r.created_at, reverse=True)
        orders_proxy = [o for o in all_my_orders if not o.is_cargo]
        orders_cargo = [o for o in all_my_orders if o.is_cargo]

        # Flow-1: orders the buyer (acting as a Proxy Buyer) has been assigned to
        # source — they respond with the estimate here.
        proxy_orders = _proxy_buying_orders(user)

    form = ProfileForm(instance=user, role=role, is_proxy_buyer=user.is_proxy_buyer)

    # Transaction detail embedded as an in-page panel (?order=<id>#order-detail).
    order = None
    order_ctx = {}
    order_id = request.GET.get("order")
    if order_id:
        order = (
            Order.objects.select_related("plan", "plan__traveler", "buyer")
            .prefetch_related("traveler_offers")
            .filter(pk=order_id)
            .first()
        )
        if order and order.plan_id is None:
            # Buyer-first order: no traveler assigned yet (or several, via legs) —
            # the buyer, the assigned proxy buyer (Flow-1), or staff see the detail
            # panel. Kept out of the `order` key (reserved for plan-first orders) so
            # the two detail panels in profile.html don't both try to render.
            is_order_proxy = bool(order.proxy_buyer_id) and order.proxy_buyer.user_id == user.id
            if user == order.buyer or is_order_proxy or user.is_staff:
                # Opened from a specific offer row — focus the accept section on
                # just that carrier's offer instead of listing every pending one.
                focus_id = request.GET.get("offer_focus")
                focus_offer = (
                    order.traveler_offers.filter(pk=focus_id, offer_status=OfferStatus.PENDING).first()
                    if focus_id else None
                )
                order_ctx = {
                    "bf_order": order,
                    "is_order_proxy": is_order_proxy,
                    "is_order_buyer": user == order.buyer,
                    "focus_offer": focus_offer,
                    "assignment_rows": (
                        _assignment_rows(order)
                        if order.is_multi_leg_cargo and user == order.buyer else None
                    ),
                    "chat_boxes": order.chat_boxes(user),
                }
            order = None
        elif order and (user in (order.buyer, order.plan.traveler) or user.is_staff):
            is_traveler = user == order.plan.traveler
            is_buyer = user == order.buyer
            order_ctx = {
                "req": order,
                "is_traveler": is_traveler,
                "is_buyer": is_buyer,
                "chat_boxes": order.chat_boxes(user),
            }
        else:
            order = None

    # Proxy estimate panel (?estimate=<id>#estimate-form) — the assigned Proxy
    # Buyer quotes per-item unit costs, total weight and margin (Flow-1).
    estimate_order = estimate_form = estimate_formset = None
    estimate_id = request.GET.get("estimate")
    if estimate_id:
        _e = Order.objects.select_related("proxy_buyer", "buyer").filter(
            pk=estimate_id, plan__isnull=True
        ).first()
        if (_e and _e.proxy_buyer_id and _e.proxy_buyer.user_id == user.id
                and not _e.is_cargo and _e.status in (Status.OPEN, Status.ESTIMATE_SENT)):
            estimate_order = _e
            estimate_form = ProxyEstimateForm(instance=_e)
            estimate_formset = ReviewItemFormSet(instance=_e, prefix="est_items")

    # Proxy "Package Ready" panel (?package_ready=<id>#package-ready) — the proxy
    # records the actual unit costs after purchasing (Flow-1 step 3).
    package_ready_order = package_ready_formset = package_ready_form = package_ready_chat_boxes = None
    package_ready_id = request.GET.get("package_ready")
    if package_ready_id:
        _p = Order.objects.select_related("proxy_buyer").filter(
            pk=package_ready_id, plan__isnull=True
        ).first()
        if (_p and _p.proxy_buyer_id and _p.proxy_buyer.user_id == user.id
                and not _p.is_cargo and _p.proxy_actuals_editable):
            package_ready_order = _p
            package_ready_formset = PurchaseItemFormSet(instance=_p)
            package_ready_form = PurchaseWeightForm(instance=_p)
            package_ready_chat_boxes = _p.chat_boxes(user)

    # Travel plan detail embedded as an in-page panel (?plan=<id>#plan-detail).
    plan = None
    plan_order_form = plan_order_formset = None
    plan_id = request.GET.get("plan")
    if plan_id:
        plan = (
            TravelPlan.objects.select_related("traveler").filter(pk=plan_id).first()
        )
        if not plan or not (user == plan.traveler or user.is_staff):
            plan = None
        else:
            plan_order_form = BuyRequestForm()
            plan_order_formset = (OrderItemFormSet if plan.carrier_only else RequestItemFormSet)(instance=Order())

    # Buyer-first leg detail as an in-page panel for the *traveler* who owns the
    # offer (?offer=<id>#offer-detail). Read-only — the traveler's actions live
    # in the My Travel Plans table; this is just a leg overview both sides can see.
    leg_offer = None
    leg_arrive_form = None
    offer_id = request.GET.get("offer")
    if offer_id:
        _o = TravelerOffer.objects.select_related("order", "order__proxy_buyer__user").filter(pk=offer_id).first()
        if _o and (user == _o.traveler or user.is_staff):
            leg_offer = _o
            leg_arrive_form = LegCustomFareForm(instance=_o)
            _ord = _o.order
            if _ord.is_chat_participant(user) or user.is_staff:
                order_ctx = {
                    "chat_boxes": _ord.chat_boxes(user),
                }

    # Review panel (?review=<id>#review-order) — traveler sends estimate.
    review_req = review_form = review_formset = review_is_edit = None
    review_id = request.GET.get("review")
    if review_id:
        _r = Order.objects.select_related("plan__traveler", "buyer").filter(
            pk=review_id, plan__traveler=user
        ).first()
        if _r and _r.status in {Status.REQUEST_RECEIVED, Status.ACCEPTED}:
            review_req = _r
            review_is_edit = _r.status == Status.ACCEPTED
            review_form = ReviewForm(instance=_r)
            review_formset = ReviewItemFormSet(instance=_r)

    # Purchase panel (?purchase=<id>#purchase-order) — traveler records purchases.
    purchase_req = purchase_form = purchase_formset = None
    purchase_id = request.GET.get("purchase")
    if purchase_id:
        _r = Order.objects.select_related("plan__traveler", "buyer").filter(pk=purchase_id).first()
        if _r and _r.status in {Status.DEPOSIT_PAID, Status.ITEMS_PURCHASED}:
            if _r.plan_id:
                is_proxy = _r.plan.traveler_id == user.id
            else:
                # Buyer-first Products (FCFS): the single live offer's traveler.
                live = [o for o in _r.traveler_offers.all()
                        if o.offer_status in (OfferStatus.PENDING, OfferStatus.SELECTED)]
                is_proxy = len(live) == 1 and live[0].traveler_id == user.id
            if is_proxy:
                purchase_req = _r
                purchase_form = PurchaseWeightForm(instance=_r)
                purchase_formset = PurchaseItemFormSet(instance=_r)

    def _is_order_traveler(_r):
        """The acting user carries this order: plan-first → the plan's carrier;
        buyer-first (cargo or Flow-1 proxy) → the single live offer's carrier."""
        if _r.plan_id:
            return _r.plan.traveler_id == user.id
        live = [o for o in _r.traveler_offers.all()
                if o.offer_status in (OfferStatus.PENDING, OfferStatus.SELECTED)]
        return len(live) == 1 and live[0].traveler_id == user.id

    # Receive panel (?receive=<id>#receive-order) — traveler confirms receipt + final
    # weight. Cargo: from the buyer at Deposit Paid. Flow-1 proxy: the handover from
    # the proxy at Items Purchased (releases the proxy's first 50%).
    receive_req = receive_form = None
    receive_id = request.GET.get("receive")
    if receive_id:
        _r = Order.objects.select_related("plan__traveler", "buyer").filter(pk=receive_id).first()
        if _r and _is_order_traveler(_r):
            if (_r.is_cargo and _r.status == Status.DEPOSIT_PAID) or (
                _r.is_proxy_buyer_first and _r.status == Status.ITEMS_PURCHASED
            ):
                receive_req = _r
                receive_form = PurchaseWeightForm(instance=_r)

    # Arrive panel (?arrive=<id>#arrive-order) — traveler marks package arrived.
    arrive_req = arrive_form = None
    arrive_id = request.GET.get("arrive")
    if arrive_id:
        _r = Order.objects.select_related("plan__traveler", "buyer").filter(pk=arrive_id).first()
        # Cargo + Flow-1 proxy arrive from Package Received (after handover);
        # plan-first proxy buying arrives straight from Items Purchased.
        _arrivable = (Status.PACKAGE_RECEIVED
                      if (_r and (_r.is_cargo or _r.is_proxy_buyer_first))
                      else Status.ITEMS_PURCHASED)
        # Proxy buyer-first: the carrier reopens this panel after the buyer pays
        # (READY_FOR_PICKUP) to record the actual duty receipt ("Actual Duty Paid").
        _receipt_followup = bool(_r and _r.is_proxy_buyer_first and _r.status == Status.READY_FOR_PICKUP)
        if _r and (_r.status == _arrivable or _receipt_followup) and _is_order_traveler(_r):
            arrive_req = _r
            arrive_form = CustomFareForm(instance=_r)

    # Reship-cost panel (?reship_cost=<id>#reship-cost-order) — traveler sends cost + bank.
    reship_cost_req = reship_cost_form = None
    reship_cost_id = request.GET.get("reship_cost")
    if reship_cost_id:
        _r = Order.objects.select_related("plan__traveler", "buyer").filter(pk=reship_cost_id).first()
        if _r and _r.status == Status.RESHIP_REQUESTED and _order_is_proxy(_r, user):
            reship_cost_req = _r
            reship_cost_form = ReshipmentCostForm(instance=_r)

    # Reship panel (?reship=<id>#reship-order) — traveler uploads AWB to ship.
    reship_req = reship_form = None
    reship_id = request.GET.get("reship")
    if reship_id:
        _r = Order.objects.select_related("plan__traveler", "buyer").filter(pk=reship_id).first()
        if _r and _r.status == Status.RESHIP_COST_SENT and _order_is_proxy(_r, user):
            reship_req = _r
            reship_form = AWBForm(instance=_r)

    # Offer-form panel (?offer=<order_id>#offer-form) — traveler responds to a buyer-first order.
    offer_form_order = offer_form_obj = None
    proxy_review_form = proxy_review_formset = None
    # An invalid offer submission stashes its POST here so we can re-render the
    # form bound (values kept + inline field errors) instead of wiping it.
    offer_post = request.session.pop("offer_form_post", None)
    offer_order_id = request.GET.get("offer")
    if offer_order_id:
        _offer_order = (
            Order.objects.prefetch_related("traveler_offers")
            .filter(pk=offer_order_id, plan__isnull=True)
            .first()
        )
        if (
            _offer_order
            and _offer_order.buyer_id != user.id
            and _offer_order.status in OPEN_ORDER_STATUSES
        ):
            route_initial = {
                "from_city": _offer_order.from_city, "from_country": _offer_order.from_country,
                "to_city": _offer_order.to_city, "to_country": _offer_order.to_country,
            }
            # Re-bind to the rejected submission only when it was for this order.
            bound = offer_post.get("data") if offer_post and str(offer_post.get("order_id")) == str(_offer_order.id) else None
            if _offer_order.is_cargo:
                # Cargo: carry offer; multiple travelers may offer (block dup self).
                if not _offer_order.traveler_offers.filter(
                    traveler=user, offer_status=OfferStatus.PENDING
                ).exists():
                    offer_form_order = _offer_order
                    offer_form_obj = TravelerOfferForm(bound, initial=route_initial) if bound else TravelerOfferForm(initial=route_initial)
                    if bound:
                        offer_form_obj.is_valid()
            elif _offer_order.status == Status.RESPONDED:
                # Flow-1 Products: carriers may keep offering while the order is
                # RESPONDED (the buyer picks one) — only block this carrier's own
                # duplicate pending offer, mirroring offer_create.
                if not _offer_order.traveler_offers.filter(
                    traveler=user, offer_status=OfferStatus.PENDING
                ).exists():
                    offer_form_order = _offer_order
                    # Offer weight defaults to the order's weight — the traveler
                    # carries exactly the buyer's cargo (no spare-baggage upsell).
                    initial = {**route_initial, "avail_kg": _offer_order.estimated_weight_kg}
                    offer_form_obj = TravelerCargoOfferForm(bound, initial=initial) if bound else TravelerCargoOfferForm(initial=initial)
                    if bound:
                        offer_form_obj.is_valid()

    # Order-form panel (?order_form=<plan_id>#order-form) — buyer places new order.
    order_form_plan = order_form_buy = order_form_formset = None
    order_form_plan_id = request.GET.get("order_form")
    if order_form_plan_id:
        from apps.pages.models import SiteSettings
        _plan = TravelPlan.objects.prefetch_related("buy_requests").filter(
            pk=order_form_plan_id
        ).first()
        min_kg = SiteSettings.load().min_remaining_weight_kg
        if (
            _plan
            and not _plan.is_closed
            and _plan.remaining_weight_kg >= min_kg
            and _plan.traveler_id != user.id
        ):
            order_form_plan = _plan
            order_form_buy = BuyRequestForm()
            order_form_formset = (OrderItemFormSet if _plan.carrier_only else RequestItemFormSet)(instance=Order())

    return render(
        request,
        "accounts/profile.html",
        {
            "role": role,
            "profile_form": form,
            "otp_form": OTPForm(),
            "password_form": ChangePasswordForm(user),
            "plan_form": TravelPlanForm(),
            "country_currency_map_json": json.dumps(ExchangeRate.country_currency_map()),
            "new_order_form": OrderForm(),
            "new_order_formset": OrderItemFormSet(instance=Order(), prefix="bf_items"),
            "travel_rows": travel_rows,
            "orders_proxy": orders_proxy,
            "orders_cargo": orders_cargo,
            "proxy_orders": proxy_orders,
            "pending_disbursements": _pending_disbursements(user),
            "estimate_order": estimate_order,
            "estimate_form": estimate_form,
            "estimate_formset": estimate_formset,
            "package_ready_order": package_ready_order,
            "package_ready_formset": package_ready_formset,
            "package_ready_form": package_ready_form,
            "package_ready_chat_boxes": package_ready_chat_boxes,
            "block_plan_id": request.GET.get("block"),
            "order": order,
            "plan": plan,
            "leg_offer": leg_offer,
            "leg_arrive_form": leg_arrive_form,
            "plan_order_form": plan_order_form,
            "plan_order_formset": plan_order_formset,
            "review_req": review_req,
            "review_form": review_form,
            "review_formset": review_formset,
            "review_is_edit": review_is_edit,
            "purchase_req": purchase_req,
            "purchase_form": purchase_form,
            "purchase_formset": purchase_formset,
            "receive_req": receive_req,
            "receive_form": receive_form,
            "arrive_req": arrive_req,
            "arrive_form": arrive_form,
            "reship_cost_req": reship_cost_req,
            "reship_cost_form": reship_cost_form,
            "reship_req": reship_req,
            "reship_form": reship_form,
            "order_form_plan": order_form_plan,
            "order_form_buy": order_form_buy,
            "order_form_formset": order_form_formset,
            "offer_form_order": offer_form_order,
            "offer_form_obj": offer_form_obj,
            "proxy_review_form": proxy_review_form,
            "proxy_review_formset": proxy_review_formset,
            **order_ctx,
        },
    )


@login_required
@require_POST
def profile_update(request):
    role = _resolve_role(request)
    form = ProfileForm(request.POST, instance=request.user, role=role, is_proxy_buyer=request.user.is_proxy_buyer)
    if form.is_valid():
        user = form.save()
        if user.phone_verified:
            messages.success(request, "Profile saved.")
        else:
            messages.success(request, "Profile saved. Please verify your WhatsApp number.")
    else:
        for field, errors in form.errors.items():
            for err in errors:
                messages.error(request, f"{field}: {err}")
    return _profile_tab("profile")


@login_required
@require_POST
def password_change(request):
    form = ChangePasswordForm(request.user, request.POST)
    if form.is_valid():
        form.save()
        # Keep the user logged in after the password hash changes.
        update_session_auth_hash(request, request.user)
        messages.success(request, "Your password was successfully changed")
    else:
        for errors in form.errors.values():
            for err in errors:
                messages.error(request, err)
    return _profile_tab("reset-password")


@login_required
@require_POST
def send_otp(request):
    user = request.user
    if not user.phone_e164:
        return JsonResponse({"ok": False, "error": "Add a phone number first."}, status=400)
    code = user.generate_phone_otp()
    send_whatsapp(
        to_user=user,
        text=f"Your ProxyBuying verification code is {code}. It expires in 10 minutes.",
        event="phone_otp",
    )
    messages.info(request, "Verification code sent to your WhatsApp.")
    return _profile_tab("profile")


@login_required
@require_POST
def verify_otp(request):
    form = OTPForm(request.POST)
    user = request.user
    if form.is_valid() and user.otp_is_valid(form.cleaned_data["code"]):
        user.phone_verified = True
        user.phone_otp = ""
        user.save(update_fields=["phone_verified", "phone_otp"])
        messages.success(request, "WhatsApp number verified. You can now post offers and requests.")
    else:
        messages.error(request, "Invalid or expired code. Please try again.")
    return _profile_tab("profile")
