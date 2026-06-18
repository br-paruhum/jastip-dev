import json

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.notifications.services import send_whatsapp
from apps.trips.constants import CHAT_STATUSES, OPEN_ORDER_STATUSES, STATUS_TONE, LegStatus, OfferStatus, Status
from apps.trips.forms import (
    AWBForm, BuyRequestForm, CustomFareForm, LegCustomFareForm, MessageForm, OrderForm,
    OrderItemFormSet, PurchaseItemFormSet, PurchaseWeightForm, ReshipmentCostForm,
    RequestItemFormSet, ReviewForm, ReviewItemFormSet, TravelPlanForm, TravelerOfferForm,
)
from apps.trips.models import BuyRequest, ExchangeRate, TravelerOffer, TravelPlan

from .forms import ChangePasswordForm, OTPForm, ProfileForm


def _profile_tab(tab):
    """Redirect back to the profile page with a given sidebar tab active."""
    return redirect(reverse("accounts:profile") + f"#{tab}")


def _resolve_role(request):
    """Current Traveler/Buyer choice for this session, self-healing into the
    session from the user's last choice if not set yet (e.g. a session that
    predates this feature, or one that was never routed through choose_role)."""
    role = request.session.get("role")
    if role not in ("traveler", "buyer"):
        role = request.user.last_role_choice or "traveler"
        request.session["role"] = role
    return role


@login_required
def choose_role(request):
    if request.method == "POST":
        role = request.POST.get("role")
        if role not in ("traveler", "buyer"):
            messages.error(request, "Please choose Traveler or Buyer.")
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
            return offer.get_leg_status_display(), STATUS_TONE.get(offer.leg_status, "muted")
        return "Awaiting drop-off", "warning"
    return offer.get_offer_status_display(), "muted"


def _travel_rows(plans, offers):
    """Combine a traveler's posted plans (one row per request-within-plan,
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
                    "ref": plan.reference, "date": plan.travel_date, "route": plan.route,
                    "available": item.available, "remaining": item.remaining,
                    "counterparty": item.req.buyer.full_name,
                    "status_label": item.req.detail_status_label, "status_tone": item.req.status_tone,
                    "req": item.req, "plan": plan, "sort_key": item.req.created_at,
                })
        else:
            rows.append({
                "kind": "plan_only", "bf_kind": "traveler_first",
                "type_label": plan.type_label, "type_is_cargo": plan.carrier_only,
                "ref": plan.reference, "date": plan.travel_date, "route": plan.route,
                "available": plan.available_weight_kg, "remaining": plan.remaining_weight_kg,
                "counterparty": None,
                "status_label": plan.get_status_display(), "status_tone": plan.status_tone,
                "plan": plan, "sort_key": plan.created_at,
            })
    for offer in offers:
        label, tone = _offer_status_display(offer)
        rows.append({
            "kind": "offer", "bf_kind": "buyer_first",
            "type_label": offer.order.counterparty_label, "type_is_cargo": offer.order.is_cargo,
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

    open_travel_rows = closed_travel_rows = []
    open_my_orders = closed_my_orders = []

    if role == "traveler":
        # Traveler side: travel plans (own initiative) merged with buyer-first
        # offers (responding to someone else's posted order) into one combined
        # "My Travel Plans" tab — each row tagged Traveler First / Buyer First by
        # how it originated, not by which button the user last clicked.
        my_plans = TravelPlan.objects.filter(traveler=user).prefetch_related("buy_requests")
        open_plans = [p for p in my_plans if not p.is_closed]
        closed_plans = [p for p in my_plans if p.is_closed]

        my_offers = TravelerOffer.objects.filter(traveler=user).select_related("order")
        closed_offer_statuses = {OfferStatus.REJECTED, OfferStatus.WITHDRAWN}
        closed_leg_statuses = {LegStatus.CLOSED, LegStatus.DROPOFF_MISSED}
        open_offer_objs = [
            o for o in my_offers
            if o.offer_status not in closed_offer_statuses and o.leg_status not in closed_leg_statuses
        ]
        closed_offer_objs = [
            o for o in my_offers
            if o.offer_status in closed_offer_statuses or o.leg_status in closed_leg_statuses
        ]
        open_travel_rows = _travel_rows(open_plans, open_offer_objs)
        closed_travel_rows = _travel_rows(closed_plans, closed_offer_objs)
    else:
        # Buyer side: plan-first requests merged with buyer-first orders into one
        # combined "My Orders" tab, tagged the same way — Traveler First if the
        # order has a linked TravelPlan, Buyer First if it doesn't.
        my_buying = list(BuyRequest.objects.filter(buyer=user, plan__isnull=False).select_related("plan"))
        my_bf_orders = list(
            BuyRequest.objects.filter(buyer=user, plan__isnull=True).prefetch_related("traveler_offers")
        )
        for r in my_buying:
            r.bf_kind = "traveler_first"
        for o in my_bf_orders:
            o.bf_kind = "buyer_first"
        all_my_orders = sorted(my_buying + my_bf_orders, key=lambda r: r.created_at, reverse=True)
        open_my_orders = [r for r in all_my_orders if r.status != Status.CLOSED]
        closed_my_orders = [r for r in all_my_orders if r.status == Status.CLOSED]

    form = ProfileForm(instance=user, role=role)

    # Transaction detail embedded as an in-page panel (?order=<id>#order-detail).
    order = None
    order_ctx = {}
    order_id = request.GET.get("order")
    if order_id:
        order = (
            BuyRequest.objects.select_related("plan", "plan__traveler", "buyer")
            .prefetch_related("traveler_offers")
            .filter(pk=order_id)
            .first()
        )
        if order and order.plan_id is None:
            # Buyer-first order: no traveler assigned yet (or several, via legs) —
            # only the buyer (or staff) sees the detail panel for now. Kept out of
            # the `order` key (reserved for plan-first orders) so the two detail
            # panels in profile.html don't both try to render.
            if user == order.buyer or user.is_staff:
                order_ctx = {"bf_order": order}
            order = None
        elif order and (user in (order.buyer, order.plan.traveler) or user.is_staff):
            is_traveler = user == order.plan.traveler
            is_buyer = user == order.buyer
            order_ctx = {
                "req": order,
                "is_traveler": is_traveler,
                "is_buyer": is_buyer,
                "chat_messages": order.messages.select_related("sender").all(),
                "message_form": MessageForm(),
                "can_chat": (is_traveler or is_buyer or user.is_staff) and order.status in CHAT_STATUSES,
            }
        else:
            order = None

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
            plan_order_formset = RequestItemFormSet(instance=BuyRequest())

    # Buyer-first leg detail as an in-page panel for the *traveler* who owns the
    # offer (?offer=<id>#offer-detail). Read-only — the traveler's actions live
    # in the My Travel Plans table; this is just a leg overview both sides can see.
    leg_offer = None
    leg_arrive_form = None
    offer_id = request.GET.get("offer")
    if offer_id:
        _o = TravelerOffer.objects.select_related("order").filter(pk=offer_id).first()
        if _o and (user == _o.traveler or user.is_staff):
            leg_offer = _o
            leg_arrive_form = LegCustomFareForm(instance=_o)

    # Review panel (?review=<id>#review-order) — traveler sends estimate.
    review_req = review_form = review_formset = review_is_edit = None
    review_id = request.GET.get("review")
    if review_id:
        _r = BuyRequest.objects.select_related("plan__traveler", "buyer").filter(
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
        _r = BuyRequest.objects.select_related("plan__traveler", "buyer").filter(
            pk=purchase_id, plan__traveler=user
        ).first()
        if _r and _r.status in {Status.DEPOSIT_PAID, Status.ITEMS_PURCHASED}:
            purchase_req = _r
            purchase_form = PurchaseWeightForm(instance=_r)
            purchase_formset = PurchaseItemFormSet(instance=_r)

    # Arrive panel (?arrive=<id>#arrive-order) — traveler marks package arrived.
    arrive_req = arrive_form = None
    arrive_id = request.GET.get("arrive")
    if arrive_id:
        _r = BuyRequest.objects.select_related("plan__traveler", "buyer").filter(
            pk=arrive_id, plan__traveler=user
        ).first()
        if _r and _r.status == Status.ITEMS_PURCHASED:
            arrive_req = _r
            arrive_form = CustomFareForm(instance=_r)

    # Reship-cost panel (?reship_cost=<id>#reship-cost-order) — traveler sends cost + bank.
    reship_cost_req = reship_cost_form = None
    reship_cost_id = request.GET.get("reship_cost")
    if reship_cost_id:
        _r = BuyRequest.objects.select_related("plan__traveler", "buyer").filter(
            pk=reship_cost_id, plan__traveler=user
        ).first()
        if _r and _r.status == Status.RESHIP_REQUESTED:
            reship_cost_req = _r
            reship_cost_form = ReshipmentCostForm(instance=_r)

    # Reship panel (?reship=<id>#reship-order) — traveler uploads AWB to ship.
    reship_req = reship_form = None
    reship_id = request.GET.get("reship")
    if reship_id:
        _r = BuyRequest.objects.select_related("plan__traveler", "buyer").filter(
            pk=reship_id, plan__traveler=user
        ).first()
        if _r and _r.status == Status.RESHIP_COST_SENT:
            reship_req = _r
            reship_form = AWBForm(instance=_r)

    # Offer-form panel (?offer=<order_id>#offer-form) — traveler places offer on a buyer-first order.
    offer_form_order = offer_form_obj = None
    offer_order_id = request.GET.get("offer")
    if offer_order_id:
        _offer_order = (
            BuyRequest.objects.prefetch_related("traveler_offers")
            .filter(pk=offer_order_id, plan__isnull=True)
            .first()
        )
        if (
            _offer_order
            and _offer_order.buyer_id != user.id
            and _offer_order.status in OPEN_ORDER_STATUSES
            and not _offer_order.traveler_offers.filter(traveler=user, offer_status=OfferStatus.PENDING).exists()
        ):
            offer_form_order = _offer_order
            offer_form_obj = TravelerOfferForm(initial={
                "from_city": _offer_order.from_city, "from_country": _offer_order.from_country,
                "to_city": _offer_order.to_city, "to_country": _offer_order.to_country,
            })

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
            and not _plan.carrier_only  # Carrier plans can't take item orders (Flow 2 pending)
        ):
            order_form_plan = _plan
            order_form_buy = BuyRequestForm()
            order_form_formset = RequestItemFormSet(instance=BuyRequest())

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
            "new_order_formset": OrderItemFormSet(instance=BuyRequest(), prefix="bf_items"),
            "open_travel_rows": open_travel_rows,
            "closed_travel_rows": closed_travel_rows,
            "open_my_orders": open_my_orders,
            "closed_my_orders": closed_my_orders,
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
            **order_ctx,
        },
    )


@login_required
@require_POST
def profile_update(request):
    role = _resolve_role(request)
    form = ProfileForm(request.POST, instance=request.user, role=role)
    if form.is_valid():
        form.save()
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
