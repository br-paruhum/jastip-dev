import json
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import workflow
from .constants import (
    CHAT_STATUSES,
    FulfillmentMethod,
    LegStatus,
    OPEN_ORDER_STATUSES,
    OPEN_PLAN_STATUSES,
    OfferStatus,
    Status,
)
from .forms import (
    AWBForm,
    BuyRequestForm,
    CustomFareForm,
    MessageForm,
    OrderForm,
    OrderItemFormSet,
    PurchaseItemFormSet,
    PurchaseWeightForm,
    RefundBankForm,
    ReshipmentCostForm,
    RequestItemFormSet,
    ReviewForm,
    ReviewItemFormSet,
    TravelerOfferForm,
    TravelPlanForm,
)
from .models import (
    BuyRequest,
    ExchangeRate,
    LegPayment,
    LegTransaction,
    Payment,
    TravelerOffer,
    TravelPlan,
    Transaction,
)


def profile_required(view):
    """Block posting offers/bids until the profile (name + WhatsApp) is complete."""

    @wraps(view)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.profile_complete:
            messages.warning(
                request, "Please complete your profile and verify your WhatsApp number first."
            )
            return redirect("accounts:profile")
        return view(request, *args, **kwargs)

    return _wrapped


# --- Traveler: create a travel plan ----------------------------------------
@profile_required
def plan_create(request):
    if request.method == "POST":
        form = TravelPlanForm(request.POST)
        if form.is_valid():
            plan = form.save(commit=False)
            plan.traveler = request.user
            plan.shipment_currency = ExchangeRate.currency_for_country(plan.from_country)
            plan.save()
            messages.success(request, "Travel plan published.")
            return redirect(reverse("accounts:profile") + "#travel-plans")
    else:
        form = TravelPlanForm()
    country_currency_map_json = json.dumps(ExchangeRate.country_currency_map())
    return render(
        request, "trips/plan_form.html",
        {"form": form, "country_currency_map_json": country_currency_map_json},
    )


def plan_detail(request, pk):
    plan = get_object_or_404(TravelPlan.objects.select_related("traveler"), pk=pk)
    plan_order_form = BuyRequestForm()
    plan_order_formset = RequestItemFormSet(instance=BuyRequest())
    return render(
        request,
        "trips/plan_detail.html",
        {"plan": plan, "plan_order_form": plan_order_form, "plan_order_formset": plan_order_formset},
    )


# --- Buyer: block a plan + compose the request ------------------------------
@profile_required
def request_create(request, plan_id):
    from apps.pages.models import SiteSettings
    plan = get_object_or_404(TravelPlan.objects.prefetch_related("buy_requests"), pk=plan_id)
    min_kg = SiteSettings.load().min_remaining_weight_kg
    if plan.is_closed or plan.remaining_weight_kg < min_kg:
        messages.error(request, "This travel plan is no longer open for requests.")
        return redirect(plan.get_absolute_url())
    if plan.traveler_id == request.user.id:
        messages.error(request, "You cannot block your own travel plan.")
        return redirect(plan.get_absolute_url())
    excluded = {Status.REJECTED, Status.CANCELLED, Status.CLOSED}
    if plan.buy_requests.filter(buyer=request.user).exclude(status__in=excluded).exists():
        messages.info(request, "You already have an active order on this travel plan.")
        return redirect(reverse("accounts:profile") + "#buying-order")

    if request.method == "POST":
        form = BuyRequestForm(request.POST)
        formset = RequestItemFormSet(request.POST, request.FILES, instance=BuyRequest())
        if form.is_valid() and formset.is_valid():
            with db_transaction.atomic():
                buy = form.save(commit=False)
                buy.plan = plan
                buy.buyer = request.user
                buy.save()
                formset.instance = buy
                items = formset.save(commit=False)
                if not items:
                    buy.delete()
                    messages.error(request, "Add at least one item.")
                    return redirect(request.path)
                for idx, item in enumerate(items, start=1):
                    item.position = idx
                    item.save()
                Transaction.objects.create(request=buy)
                workflow.on_request_submitted(buy)
            messages.success(request, "Request sent to the traveler.")
            return redirect(reverse("accounts:profile") + f"?order={buy.id}#order-detail")
    else:
        form = BuyRequestForm()
        formset = RequestItemFormSet(instance=BuyRequest())
    return render(request, "trips/request_form.html", {"plan": plan, "form": form, "formset": formset})


# --- Buyer-first: post an order with no traveler yet ------------------------
@profile_required
def order_create(request):
    if request.method == "POST":
        form = OrderForm(request.POST)
        formset = OrderItemFormSet(request.POST, request.FILES, instance=BuyRequest(), prefix="bf_items")
        if form.is_valid() and formset.is_valid():
            with db_transaction.atomic():
                order = form.save(commit=False)
                order.buyer = request.user
                order.status = Status.OPEN
                order.settlement_currency = ExchangeRate.currency_for_country(order.from_country)
                order.save()
                formset.instance = order
                items = formset.save(commit=False)
                if not items:
                    order.delete()
                    messages.error(request, "Add at least one item.")
                    return redirect(request.path)
                for idx, item in enumerate(items, start=1):
                    item.position = idx
                    # No traveler "purchase" step in this flow — the buyer's
                    # declared qty/price stand as final for the customs invoice.
                    item.actual_quantity = item.quantity
                    item.actual_unit_cost = item.estimated_unit_cost
                    item.purchased_at = timezone.now()
                    item.save()
            messages.success(request, "Order posted. Travelers can now respond with offers.")
            return redirect(reverse("accounts:profile") + f"?order={order.id}#order-detail")
    else:
        form = OrderForm()
        formset = OrderItemFormSet(instance=BuyRequest(), prefix="bf_items")
    return render(request, "trips/order_form.html", {"form": form, "formset": formset})


# --- Traveler: respond to a buyer-first order with an offer ----------------
@profile_required
@require_POST
def offer_create(request, order_id):
    dashboard_url = reverse("accounts:profile")
    order = get_object_or_404(BuyRequest, pk=order_id, plan__isnull=True)
    if order.buyer_id == request.user.id:
        messages.error(request, "You cannot place an offer on your own order.")
        return redirect(reverse("pages:home") + "#open-orders")
    if order.status not in OPEN_ORDER_STATUSES:
        messages.info(request, "This order is no longer accepting offers.")
        return redirect(dashboard_url + "#travel-plans")
    if order.traveler_offers.filter(traveler=request.user, offer_status=OfferStatus.PENDING).exists():
        messages.info(request, "You already have a pending offer on this order. Withdraw it first to submit a new one.")
        return redirect(dashboard_url + "#travel-plans")

    form = TravelerOfferForm(request.POST)
    if form.is_valid():
        offer = form.save(commit=False)
        offer.order = order
        offer.traveler = request.user
        offer.pickup_address = request.user.traveler_address
        offer.save()
        order.recompute_status()
        workflow.on_offer_submitted(order, offer)
        messages.success(request, "Offer submitted. The buyer will review it.")
        return redirect(dashboard_url + "#travel-plans")
    for field, errs in form.errors.items():
        for err in errs:
            messages.error(request, f"{field}: {err}")
    return redirect(dashboard_url + f"?offer={order_id}#offer-form")


# --- Traveler: withdraw a pending offer -------------------------------------
@profile_required
@require_POST
def offer_withdraw(request, pk):
    offer = get_object_or_404(TravelerOffer, pk=pk, traveler=request.user)
    if offer.offer_status != OfferStatus.PENDING:
        messages.error(request, "Only a pending offer can be withdrawn.")
    else:
        offer.offer_status = OfferStatus.WITHDRAWN
        offer.save(update_fields=["offer_status", "updated_at"])
        offer.order.recompute_status()
        messages.success(request, "Offer withdrawn.")
    return redirect(reverse("pages:home") + "#open-orders")


# --- Buyer: select a pending offer (single or partial-multi) ----------------
@profile_required
@require_POST
def offer_select(request, pk):
    offer = get_object_or_404(TravelerOffer, pk=pk, offer_status=OfferStatus.PENDING)
    order = offer.order
    detail_url = reverse("accounts:profile") + f"?order={order.id}#order-detail"
    if request.user != order.buyer:
        messages.error(request, "Only the buyer can select an offer.")
        return redirect(detail_url)
    if order.status not in OPEN_ORDER_STATUSES:
        messages.error(request, "This order is no longer accepting selections.")
        return redirect(detail_url)

    remaining = order.bid_weight_kg - order.total_allocated_weight_kg
    try:
        allocated = Decimal(request.POST.get("allocated_weight_kg", "0"))
    except InvalidOperation:
        allocated = Decimal("0")

    if order.partial_allowed:
        max_allowed = min(offer.avail_kg, remaining)
    else:
        max_allowed = remaining  # must take the whole remaining bid in one go

    if allocated <= 0 or allocated > max_allowed:
        messages.error(
            request,
            f"Allocated weight must be between 0 and {max_allowed} kg"
            + ("" if order.partial_allowed else " (partial fulfillment is not allowed for this order)."),
        )
        return redirect(detail_url)

    with db_transaction.atomic():
        offer.offer_status = OfferStatus.SELECTED
        offer.allocated_weight_kg = allocated
        offer.save(update_fields=["offer_status", "allocated_weight_kg", "updated_at"])
        LegTransaction.objects.get_or_create(leg=offer)
        order.recompute_status()
    messages.success(request, "Offer selected. Pay this leg's deposit to reveal the traveler's drop-off address.")
    return redirect(detail_url)


# --- Buyer: upload a leg's deposit payment proof -----------------------------
@profile_required
@require_POST
def leg_deposit_pay(request, pk):
    offer = get_object_or_404(TravelerOffer, pk=pk, offer_status=OfferStatus.SELECTED)
    order = offer.order
    detail_url = reverse("accounts:profile") + f"?order={order.id}#order-detail"
    if request.user != order.buyer:
        messages.error(request, "Only the buyer can submit a payment.")
        return redirect(detail_url)
    if offer.deposit_verified:
        messages.info(request, "This leg's deposit is already verified.")
        return redirect(detail_url)

    tx, _ = LegTransaction.objects.get_or_create(leg=offer)
    LegPayment.objects.create(
        transaction=tx,
        direction=LegPayment.Direction.INBOUND,
        kind=LegPayment.Kind.DEPOSIT,
        currency=order.currency,
        amount=offer.deposit_due,
        proof=request.FILES.get("proof"),
        note=request.POST.get("note", ""),
    )
    messages.success(request, "Deposit proof submitted. Admin will verify it shortly.")
    return redirect(detail_url)


# --- Buyer: mark a confirmed leg's package as dropped off --------------------
@profile_required
@require_POST
def leg_dropped_off(request, pk):
    offer = get_object_or_404(TravelerOffer, pk=pk, offer_status=OfferStatus.SELECTED)
    order = offer.order
    detail_url = reverse("accounts:profile") + f"?order={order.id}#order-detail"
    if request.user != order.buyer:
        messages.error(request, "Only the buyer can mark a package as dropped off.")
        return redirect(detail_url)
    if not offer.address_revealed:
        messages.error(request, "Pay this leg's deposit first.")
        return redirect(detail_url)
    if offer.leg_status:
        messages.info(request, "This leg has already moved past drop-off.")
        return redirect(detail_url)

    offer.leg_status = LegStatus.PACKAGE_DROPPED_OFF
    offer.dropped_off_at = timezone.now()
    offer.save(update_fields=["leg_status", "dropped_off_at", "updated_at"])
    order.recompute_status()
    messages.success(request, "Package marked as dropped off. The traveler will verify the final weight.")
    return redirect(detail_url)


# --- Traveler: enter the final weight for a dropped-off leg -------------------
@profile_required
@require_POST
def leg_weight_verify(request, pk):
    offer = get_object_or_404(
        TravelerOffer, pk=pk, traveler=request.user, leg_status=LegStatus.PACKAGE_DROPPED_OFF
    )
    detail_url = reverse("accounts:profile") + "#my-offers"
    try:
        weight = Decimal(request.POST.get("agreed_weight_kg", "0"))
    except InvalidOperation:
        weight = Decimal("0")
    if weight <= 0:
        messages.error(request, "Enter a valid final weight.")
        return redirect(detail_url)

    offer.agreed_weight_kg = weight
    offer.leg_status = LegStatus.WEIGHT_VERIFIED
    offer.weight_verified_at = timezone.now()
    offer.save(update_fields=["agreed_weight_kg", "leg_status", "weight_verified_at", "updated_at"])
    offer.order.recompute_status()
    messages.success(request, "Final weight recorded — this is final and not subject to dispute.")
    return redirect(detail_url)


# --- Traveler: confirm custody of a weight-verified leg ------------------------
@profile_required
@require_POST
def leg_received(request, pk):
    offer = get_object_or_404(
        TravelerOffer, pk=pk, traveler=request.user, leg_status=LegStatus.WEIGHT_VERIFIED
    )
    detail_url = reverse("accounts:profile") + "#my-offers"
    offer.leg_status = LegStatus.PACKAGE_RECEIVED
    offer.received_at = timezone.now()
    offer.save(update_fields=["leg_status", "received_at", "updated_at"])
    offer.order.recompute_status()
    messages.success(request, "Package received — custody confirmed.")
    return redirect(detail_url)


# --- Traveler: mark a received leg as arrived at destination -----------------
@profile_required
@require_POST
def leg_arrived(request, pk):
    offer = get_object_or_404(
        TravelerOffer, pk=pk, traveler=request.user, leg_status=LegStatus.PACKAGE_RECEIVED
    )
    detail_url = reverse("accounts:profile") + "#my-offers"
    offer.leg_status = LegStatus.PACKAGE_ARRIVED
    offer.arrived_at = timezone.now()
    offer.save(update_fields=["leg_status", "arrived_at", "updated_at"])
    offer.order.recompute_status()
    messages.success(
        request,
        "Marked as arrived. The buyer will settle any weight-delta balance and choose pickup or reship.",
    )
    return redirect(detail_url)


# --- Buyer: pay a leg's weight-delta balance ---------------------------------
@profile_required
@require_POST
def leg_balance_pay(request, pk):
    offer = get_object_or_404(TravelerOffer, pk=pk, leg_status=LegStatus.PACKAGE_ARRIVED)
    order = offer.order
    detail_url = reverse("accounts:profile") + f"?order={order.id}#order-detail"
    if request.user != order.buyer:
        messages.error(request, "Only the buyer can submit a payment.")
        return redirect(detail_url)
    if offer.extra_due <= 0:
        messages.info(request, "No balance is due on this leg.")
        return redirect(detail_url)
    if offer.balance_settled:
        messages.info(request, "This leg's balance is already settled.")
        return redirect(detail_url)

    tx, _ = LegTransaction.objects.get_or_create(leg=offer)
    LegPayment.objects.create(
        transaction=tx,
        direction=LegPayment.Direction.INBOUND,
        kind=LegPayment.Kind.BALANCE,
        currency=order.currency,
        amount=offer.extra_due,
        proof=request.FILES.get("proof"),
        note=request.POST.get("note", ""),
    )
    messages.success(request, "Balance payment proof submitted. Admin will verify it shortly.")
    return redirect(detail_url)


# --- Buyer: submit bank details for a leg's refund ----------------------------
@profile_required
@require_POST
def leg_refund_bank(request, pk):
    offer = get_object_or_404(
        TravelerOffer, pk=pk, leg_status__in=[LegStatus.PACKAGE_ARRIVED, LegStatus.DROPOFF_MISSED]
    )
    order = offer.order
    detail_url = reverse("accounts:profile") + f"?order={order.id}#order-detail"
    if request.user != order.buyer:
        messages.error(request, "Only the buyer can submit refund details.")
        return redirect(detail_url)
    if offer.leg_status == LegStatus.PACKAGE_ARRIVED:
        no_refund = offer.refund_due <= 0
    else:
        no_refund = offer.dropoff_refund_amount <= 0
    if no_refund:
        messages.error(request, "No refund is due on this leg.")
        return redirect(detail_url)

    details = request.POST.get("refund_bank_details", "").strip()
    if not details:
        messages.error(request, "Enter your bank details to receive the refund.")
        return redirect(detail_url)
    offer.refund_bank_details = details
    offer.save(update_fields=["refund_bank_details", "updated_at"])
    messages.success(request, "Refund bank details saved. Admin will transfer the refund shortly.")
    return redirect(detail_url)


# --- Buyer: choose Pickup or Reship for an arrived leg ------------------------
@profile_required
@require_POST
def leg_choose_fulfillment(request, pk):
    offer = get_object_or_404(TravelerOffer, pk=pk, leg_status=LegStatus.PACKAGE_ARRIVED)
    order = offer.order
    detail_url = reverse("accounts:profile") + f"?order={order.id}#order-detail"
    if request.user != order.buyer:
        messages.error(request, "Only the buyer can choose pickup or reship.")
        return redirect(detail_url)
    if not offer.balance_settled:
        messages.error(request, "Settle this leg's weight-delta balance first.")
        return redirect(detail_url)

    choice = request.POST.get("fulfillment_method")
    if choice == FulfillmentMethod.PICKUP:
        offer.fulfillment_method = FulfillmentMethod.PICKUP
        offer.leg_status = LegStatus.READY_FOR_PICKUP
        offer.save(update_fields=["fulfillment_method", "leg_status", "updated_at"])
        order.recompute_status()
        messages.success(request, "Pickup confirmed. The traveler's pickup address is now shown below.")
    elif choice == FulfillmentMethod.RESHIP:
        address = request.POST.get("reshipment_address", "").strip()
        if not address:
            messages.error(request, "Enter your delivery address to request reshipment.")
            return redirect(detail_url)
        offer.fulfillment_method = FulfillmentMethod.RESHIP
        offer.reshipment_address = address
        offer.leg_status = LegStatus.RESHIP_REQUESTED
        offer.save(update_fields=["fulfillment_method", "reshipment_address", "leg_status", "updated_at"])
        order.recompute_status()
        messages.success(request, "Reshipment requested. The traveler has been notified.")
    else:
        messages.error(request, "Choose Pickup or Reship.")
    return redirect(detail_url)


# --- Traveler: send a leg's reshipment cost + bank details --------------------
@profile_required
@require_POST
def leg_reship_cost(request, pk):
    offer = get_object_or_404(
        TravelerOffer, pk=pk, traveler=request.user, leg_status=LegStatus.RESHIP_REQUESTED
    )
    detail_url = reverse("accounts:profile") + "#my-offers"
    try:
        amount = Decimal(request.POST.get("reshipment_cost_amount", "0"))
    except InvalidOperation:
        amount = Decimal("0")
    if amount <= 0:
        messages.error(request, "Enter the reshipment cost.")
        return redirect(detail_url)

    offer.reshipment_cost_amount = amount
    offer.reshipment_bank_name = request.POST.get("reshipment_bank_name", "")
    offer.reshipment_bank_account_no = request.POST.get("reshipment_bank_account_no", "")
    offer.reshipment_bank_account_name = request.POST.get("reshipment_bank_account_name", "")
    if request.FILES.get("reshipment_cost_proof"):
        offer.reshipment_cost_proof = request.FILES["reshipment_cost_proof"]
    offer.leg_status = LegStatus.RESHIP_COST_SENT
    offer.save(update_fields=[
        "reshipment_cost_amount", "reshipment_bank_name", "reshipment_bank_account_no",
        "reshipment_bank_account_name", "reshipment_cost_proof", "leg_status", "updated_at",
    ])
    offer.order.recompute_status()
    messages.success(request, "Reshipment cost sent. Buyer has been notified.")
    return redirect(detail_url)


# --- Buyer: upload a leg's reshipment payment proof ---------------------------
@profile_required
@require_POST
def leg_reship_proof(request, pk):
    offer = get_object_or_404(TravelerOffer, pk=pk, leg_status=LegStatus.RESHIP_COST_SENT)
    order = offer.order
    detail_url = reverse("accounts:profile") + f"?order={order.id}#order-detail"
    if request.user != order.buyer:
        messages.error(request, "Only the buyer can upload reshipment proof.")
        return redirect(detail_url)
    proof = request.FILES.get("reshipment_proof")
    if not proof:
        messages.error(request, "Please select a file to upload.")
        return redirect(detail_url)
    offer.reshipment_proof = proof
    offer.save(update_fields=["reshipment_proof", "updated_at"])
    messages.success(request, "Payment proof uploaded. Traveler has been notified.")
    return redirect(detail_url)


# --- Traveler: submit a leg's AWB -> shipped -----------------------------------
@profile_required
@require_POST
def leg_reship_ship(request, pk):
    offer = get_object_or_404(
        TravelerOffer, pk=pk, traveler=request.user, leg_status=LegStatus.RESHIP_COST_SENT
    )
    detail_url = reverse("accounts:profile") + "#my-offers"
    awb = request.POST.get("awb_number", "").strip()
    if not awb:
        messages.error(request, "Enter an AWB number before submitting.")
        return redirect(detail_url)
    offer.awb_number = awb
    if request.FILES.get("awb_document"):
        offer.awb_document = request.FILES["awb_document"]
    offer.leg_status = LegStatus.RESHIPPING
    offer.save(update_fields=["awb_number", "awb_document", "leg_status", "updated_at"])
    offer.order.recompute_status()
    messages.success(request, "Package marked as shipped. Buyer has been notified.")
    return redirect(detail_url)


# --- Buyer: confirm a leg is Clear (picked up / received after reship) -------
@profile_required
@require_POST
def leg_clear(request, pk):
    offer = get_object_or_404(
        TravelerOffer, pk=pk, leg_status__in=[LegStatus.READY_FOR_PICKUP, LegStatus.RESHIPPING]
    )
    order = offer.order
    detail_url = reverse("accounts:profile") + f"?order={order.id}#order-detail"
    if request.user != order.buyer:
        messages.error(request, "Only the buyer can confirm pickup and clearance.")
        return redirect(detail_url)

    offer.leg_status = LegStatus.CLEAR
    offer.cleared_at = timezone.now()
    offer.save(update_fields=["leg_status", "cleared_at", "updated_at"])
    order.recompute_status()
    messages.success(
        request,
        "Thank you — marked as Clear. This leg will close automatically and the traveler will be paid shortly.",
    )
    return redirect(detail_url)


def request_detail(request, pk):
    req = get_object_or_404(
        BuyRequest.objects.select_related("plan", "plan__traveler", "buyer"), pk=pk
    )
    if request.user not in (req.buyer, req.plan.traveler) and not request.user.is_staff:
        messages.error(request, "You do not have access to this request.")
        return redirect("pages:home")
    is_traveler = request.user == req.plan.traveler
    is_buyer = request.user == req.buyer
    chat_messages = req.messages.select_related("sender").all()
    return render(
        request,
        "trips/request_detail.html",
        {
            "req": req,
            "is_traveler": is_traveler,
            "is_buyer": is_buyer,
            "chat_messages": chat_messages,
            "message_form": MessageForm(),
            "can_chat": (is_traveler or is_buyer or request.user.is_staff) and req.status in CHAT_STATUSES,
            "refund_form": RefundBankForm(instance=req),
        },
    )


@profile_required
@require_POST
def request_refund_bank(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if request.user != req.buyer:
        messages.error(request, "Only the buyer can submit refund details.")
        return redirect(req.get_absolute_url())
    if req.refund_due <= 0:
        messages.error(request, "No refund is due on this request.")
        return redirect(req.get_absolute_url())
    form = RefundBankForm(request.POST, instance=req)
    if form.is_valid():
        form.save()
        messages.success(request, "Refund bank details saved. Admin will transfer the overpaid amount within 48 hours.")
    else:
        messages.error(request, "Please fill in all bank detail fields.")
    return redirect(req.get_absolute_url())


@login_required
@require_POST
def request_message(request, pk):
    req = get_object_or_404(BuyRequest.objects.select_related("plan", "plan__traveler", "buyer"), pk=pk)
    if request.user not in (req.buyer, req.plan.traveler) and not request.user.is_staff:
        messages.error(request, "You cannot post to this conversation.")
        return redirect("pages:home")
    if req.status not in CHAT_STATUSES and not request.user.is_staff:
        messages.error(request, "Chat is not available at this stage.")
        return redirect(req.get_absolute_url())
    form = MessageForm(request.POST, request.FILES)
    if form.is_valid():
        msg = form.save(commit=False)
        msg.request = req
        msg.sender = request.user
        msg.save()
        workflow.on_new_message(msg)
    else:
        messages.error(request, "Message cannot be empty.")
    return redirect(req.get_absolute_url())


def _require_traveler(request, req):
    return request.user == req.plan.traveler


# --- Traveler: review (price + accept/reject) -------------------------------
@profile_required
def request_review(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if not _require_traveler(request, req):
        messages.error(request, "Only the traveler can review this request.")
        return redirect(req.get_absolute_url())
    if req.status not in {Status.REQUEST_RECEIVED, Status.ACCEPTED}:
        messages.info(request, "This request can no longer be edited.")
        return redirect(req.get_absolute_url())

    is_edit = req.status == Status.ACCEPTED

    if request.method == "POST":
        form = ReviewForm(request.POST, instance=req)
        formset = ReviewItemFormSet(request.POST, instance=req)
        decision = request.POST.get("decision")
        if form.is_valid() and formset.is_valid():
            if decision == "accept" and (form.cleaned_data.get("estimated_weight_kg") or 0) <= 0:
                messages.error(request, "Enter the estimated package weight (kg) before accepting.")
            else:
                form.save()
                formset.save()
                if decision == "accept":
                    if is_edit:
                        messages.success(request, "Estimate updated.")
                    else:
                        workflow.on_request_accepted(req)
                        messages.success(request, "Estimate sent. The buyer has been notified.")
                    return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")
                elif decision == "reject":
                    workflow.on_request_rejected(req, reason=request.POST.get("rejection_reason", ""))
                    messages.success(request, "Request rejected. The plan is open again.")
                    return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")
                elif decision == "draft":
                    messages.success(request, "Draft saved. Come back to submit or reject anytime.")
                    return redirect(reverse("accounts:profile") + f"?review={req.id}#review-order")
                else:
                    messages.error(request, "Choose Submit Estimate, Reject, or Save Draft.")
    else:
        form = ReviewForm(instance=req)
        formset = ReviewItemFormSet(instance=req)
    return render(request, "trips/request_review.html", {"req": req, "form": form, "formset": formset, "is_edit": is_edit})


# --- Traveler: record purchases -> Item(s) Purchased ------------------------
@profile_required
def request_purchase(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if not _require_traveler(request, req):
        messages.error(request, "Only the traveler can record purchases.")
        return redirect(req.get_absolute_url())
    if req.status not in {Status.DEPOSIT_PAID, Status.ITEMS_PURCHASED}:
        messages.info(request, "Purchases can be recorded after the deposit is paid.")
        return redirect(req.get_absolute_url())

    if request.method == "POST":
        form = PurchaseWeightForm(request.POST, instance=req)  # actual shipment weight
        formset = PurchaseItemFormSet(request.POST, request.FILES, instance=req)
        decision = request.POST.get("decision", "finalize")
        if form.is_valid() and formset.is_valid():
            form.save()
            items = formset.save(commit=False)
            for item in items:
                if item.actual_unit_cost and not item.purchased_at:
                    item.purchased_at = timezone.now()
                item.save()
            if decision == "draft":
                messages.success(request, "Purchase draft saved. The buyer hasn't been notified yet.")
                return redirect(reverse("accounts:profile") + f"?purchase={req.id}#purchase-order")
            workflow.on_items_purchased(req)
            messages.success(request, "Purchases recorded. Invoice updated and buyer notified.")
            return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")
    else:
        form = PurchaseWeightForm(instance=req)
        formset = PurchaseItemFormSet(instance=req)
    return render(request, "trips/request_purchase.html", {"req": req, "form": form, "formset": formset})


# --- Traveler: arrival + custom fare -> Package Arrived ---------------------
@profile_required
def request_arrive(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if not _require_traveler(request, req):
        messages.error(request, "Only the traveler can update arrival.")
        return redirect(req.get_absolute_url())
    if req.status != Status.ITEMS_PURCHASED:
        messages.info(request, "You can mark arrival after items are purchased.")
        return redirect(req.get_absolute_url())

    if request.method == "POST":
        form = CustomFareForm(request.POST, request.FILES, instance=req)
        if form.is_valid():
            form.save()
            workflow.on_package_arrived(req)
            messages.success(request, "Marked as arrived. The buyer has been notified to pay the balance.")
            return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")
    else:
        form = CustomFareForm(instance=req)
    return render(request, "trips/request_arrive.html", {"req": req, "form": form})


# --- Buyer: upload a payment proof (deposit or balance) ---------------------
@profile_required
@require_POST
def request_pay(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if request.user != req.buyer:
        messages.error(request, "Only the buyer can submit a payment.")
        return redirect(req.get_absolute_url())

    tx, _ = Transaction.objects.get_or_create(request=req)
    if req.status == Status.ACCEPTED:
        kind, amount = Payment.Kind.DEPOSIT, req.deposit_due
    elif req.status == Status.PACKAGE_ARRIVED:
        kind, amount = Payment.Kind.BALANCE, req.unpaid_amount
    else:
        messages.error(request, "No payment is due at this stage.")
        return redirect(req.get_absolute_url())

    Payment.objects.create(
        transaction=tx,
        direction=Payment.Direction.INBOUND,
        kind=kind,
        currency=req.currency,
        amount=amount,
        proof=request.FILES.get("proof"),
        note=request.POST.get("note", ""),
    )
    messages.success(request, "Payment proof submitted. Admin will verify it shortly.")
    return redirect(req.get_absolute_url())


# --- Clearance: buyer marks Clear; cron closes it next day ------------------
@profile_required
@require_POST
def request_clear(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if request.user != req.buyer:
        messages.error(request, "Only the buyer can confirm pickup and clearance.")
        return redirect(req.get_absolute_url())
    if req.status not in {Status.READY_FOR_PICKUP, Status.RESHIPPING}:
        messages.error(request, "Clearance is only available at Paid in Full or In Transit stage.")
        return redirect(req.get_absolute_url())

    workflow.on_buyer_cleared(req)
    messages.success(
        request,
        "Thank you — marked as Clear. The transaction will close automatically and the "
        "traveler will be paid shortly.",
    )
    return redirect(req.get_absolute_url())


# --- Buyer: request reshipment, using the address saved on their profile ----
@profile_required
@require_POST
def request_reship_request(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if request.user != req.buyer:
        messages.error(request, "Only the buyer can request reshipment.")
        return redirect(req.get_absolute_url())
    if req.status != Status.READY_FOR_PICKUP:
        messages.error(request, "Reshipment can only be requested at Paid in Full stage.")
        return redirect(req.get_absolute_url())
    address = req.buyer.buyer_invoice_address.strip()
    if not address:
        messages.error(request, "Please fill in your Reshipment Address on your Profile page first.")
        return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")
    req.reshipment_address = address
    req.save(update_fields=["reshipment_address", "updated_at"])
    workflow.on_reship_requested(req)
    messages.success(request, "Reshipment requested. The traveler has been notified.")
    return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")


# --- Traveler: send reshipment cost + bank details --------------------------
@profile_required
def request_reship_cost(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if not _require_traveler(request, req):
        messages.error(request, "Only the traveler can submit the reshipment cost.")
        return redirect(req.get_absolute_url())
    if req.status != Status.RESHIP_REQUESTED:
        messages.info(request, "No reshipment cost to submit at this stage.")
        return redirect(req.get_absolute_url())

    if request.method == "POST":
        form = ReshipmentCostForm(request.POST, request.FILES, instance=req)
        if form.is_valid():
            form.save()
            workflow.on_reship_cost_sent(req)
            messages.success(request, "Cost sent. Buyer has been notified.")
            return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")
        else:
            messages.error(request, "Please fill in all required fields.")
    return redirect(reverse("accounts:profile") + f"?reship_cost={req.id}#reship-cost-order")


# --- Traveler: submit AWB → In Transit --------------------------------------
@profile_required
def request_reship(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if not _require_traveler(request, req):
        messages.error(request, "Only the traveler can mark this as shipped.")
        return redirect(req.get_absolute_url())
    if req.status != Status.RESHIP_COST_SENT:
        messages.info(request, "AWB can only be submitted after reshipment cost is sent.")
        return redirect(req.get_absolute_url())

    if request.method == "POST":
        form = AWBForm(request.POST, request.FILES, instance=req)
        if form.is_valid():
            form.save()
            workflow.on_reshipped(req)
            messages.success(request, "Package marked as shipped. Buyer has been notified.")
            return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")
        else:
            messages.error(request, "Please enter an AWB number before submitting.")
    return redirect(reverse("accounts:profile") + f"?reship={req.id}#reship-order")


# --- Buyer: upload reshipment payment proof ---------------------------------
@profile_required
@require_POST
def request_reship_proof(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if request.user != req.buyer:
        messages.error(request, "Only the buyer can upload reshipment proof.")
        return redirect(req.get_absolute_url())
    if req.status != Status.RESHIP_COST_SENT:
        messages.error(request, "No reshipment proof is needed at this stage.")
        return redirect(req.get_absolute_url())
    proof = request.FILES.get("reshipment_proof")
    if proof:
        req.reshipment_proof = proof
        req.save(update_fields=["reshipment_proof", "updated_at"])
        workflow.on_reship_proof_uploaded(req)
        messages.success(request, "Payment proof uploaded. Traveler has been notified.")
    else:
        messages.error(request, "Please select a file to upload.")
    return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")


@login_required
def kurs(request):
    rates = ExchangeRate.objects.filter(is_active=True).order_by("sequence", "code")
    last_updated = rates.order_by("-updated_at").values_list("updated_at", flat=True).first()
    return render(request, "trips/kurs.html", {"rates": rates, "last_updated": last_updated})


@login_required
def request_customs_invoice(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    allowed = (
        request.user.is_staff
        or req.buyer_id == request.user.id
        or (req.plan_id and req.plan.traveler_id == request.user.id)
        or any(leg.traveler_id == request.user.id for leg in req.confirmed_legs)
    )
    if not allowed:
        messages.error(request, "You don't have access to this customs invoice.")
        return redirect(reverse("accounts:profile"))
    if not req.customs_invoice_available:
        messages.error(request, "Customs invoice is not available yet.")
        return redirect(req.get_absolute_url())
    return render(request, "trips/customs_invoice_print.html", {"req": req})
