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
from . import flow_types
from apps.notifications.services import send_email, send_whatsapp
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
    LegCustomFareForm,
    MessageForm,
    OrderForm,
    OrderItemFormSet,
    ProxyEstimateForm,
    ProxyOfferForm,
    TravelerCargoOfferForm,
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
    ItemLegAllocation,
    LegPayment,
    LegTransaction,
    Payment,
    ProxyBuyer,
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


@profile_required
def plan_edit(request, pk):
    plan = get_object_or_404(TravelPlan, pk=pk)
    if plan.traveler_id != request.user.id:
        messages.error(request, "You can only edit your own travel plan.")
        return redirect(reverse("accounts:profile") + "#travel-plans")
    if not plan.can_edit:
        messages.error(request, "This travel plan can no longer be edited.")
        return redirect(reverse("accounts:profile") + "#travel-plans")
    if request.method == "POST":
        form = TravelPlanForm(request.POST, instance=plan)
        if form.is_valid():
            plan = form.save(commit=False)
            plan.shipment_currency = ExchangeRate.currency_for_country(plan.from_country)
            plan.save()
            messages.success(request, "Travel plan updated.")
            return redirect(reverse("accounts:profile") + "#travel-plans")
    else:
        form = TravelPlanForm(instance=plan)
    country_currency_map_json = json.dumps(ExchangeRate.country_currency_map())
    return render(
        request, "trips/plan_form.html",
        {"form": form, "country_currency_map_json": country_currency_map_json, "is_edit": True, "plan": plan},
    )


@profile_required
@require_POST
def plan_cancel(request, pk):
    plan = get_object_or_404(TravelPlan, pk=pk)
    if plan.traveler_id != request.user.id:
        messages.error(request, "You can only cancel your own travel plan.")
        return redirect(reverse("accounts:profile") + "#travel-plans")
    if not plan.can_edit:
        messages.error(request, "This travel plan can no longer be cancelled.")
        return redirect(reverse("accounts:profile") + "#travel-plans")
    plan.status = Status.CANCELLED
    plan.save(update_fields=["status", "updated_at"])
    messages.success(request, "Travel plan cancelled.")
    return redirect(reverse("accounts:profile") + "#travel-plans")


def plan_detail(request, pk):
    plan = get_object_or_404(TravelPlan.objects.select_related("traveler"), pk=pk)
    plan_order_form = BuyRequestForm()
    _ItemFormSet = OrderItemFormSet if plan.carrier_only else RequestItemFormSet
    plan_order_formset = _ItemFormSet(instance=BuyRequest())
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
    # Flow 2 (Phase 2a): Carrier plans now accept a cargo order (declared contents
    # + weight, no purchase). The order's is_cargo follows the plan, so the same
    # form is reused; the carry tail diverges from Flow 1 after acceptance.
    excluded = {Status.REJECTED, Status.CANCELLED, Status.CLOSED}
    if plan.buy_requests.filter(buyer=request.user).exclude(status__in=excluded).exists():
        messages.info(request, "You already have an active order on this travel plan.")
        return redirect(reverse("accounts:profile") + "#buying-order")

    # Cargo (Carrier plan): the buyer owns the goods and declares the unit cost
    # upfront for the customs invoice (no traveler purchase step), so use the
    # item form that exposes the unit price — same as the buyer-first cargo flow.
    ItemFormSet = OrderItemFormSet if plan.carrier_only else RequestItemFormSet

    if request.method == "POST":
        form = BuyRequestForm(request.POST)
        formset = ItemFormSet(request.POST, request.FILES, instance=BuyRequest())
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
                    if plan.carrier_only:
                        # No purchase step — the buyer's declared qty/price stand
                        # as final for the customs invoice.
                        item.actual_quantity = item.quantity
                        item.actual_unit_cost = item.estimated_unit_cost
                        item.purchased_at = timezone.now()
                    item.save()
                Transaction.objects.create(request=buy)
                workflow.on_request_submitted(buy)
            messages.success(request, "Cargo order sent to the carrier." if plan.carrier_only else "Request sent to the traveler.")
            return redirect(reverse("accounts:profile") + f"?order={buy.id}#order-detail")
    else:
        form = BuyRequestForm()
        formset = ItemFormSet(instance=BuyRequest())
    return render(request, "trips/request_form.html", {"plan": plan, "form": form, "formset": formset})


# --- Buyer: edit / cancel a plan-first order before the traveler acts -------
@profile_required
def request_edit(request, pk):
    req = get_object_or_404(BuyRequest.objects.select_related("plan"), pk=pk)
    if req.buyer_id != request.user.id:
        messages.error(request, "You can only edit your own order.")
        return redirect(reverse("accounts:profile") + "#my-orders")
    if not req.can_edit:
        messages.error(request, "This order can no longer be edited.")
        return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")
    plan = req.plan
    ItemFormSet = OrderItemFormSet if plan.carrier_only else RequestItemFormSet
    if request.method == "POST":
        form = BuyRequestForm(request.POST, instance=req)
        formset = ItemFormSet(request.POST, request.FILES, instance=req)
        if form.is_valid() and formset.is_valid():
            with db_transaction.atomic():
                buy = form.save()
                formset.save()
                items = list(buy.items.order_by("position", "id"))
                if not items:
                    db_transaction.set_rollback(True)
                else:
                    for idx, item in enumerate(items, start=1):
                        item.position = idx
                        if plan.carrier_only:
                            item.actual_quantity = item.quantity
                            item.actual_unit_cost = item.estimated_unit_cost
                            if not item.purchased_at:
                                item.purchased_at = timezone.now()
                        item.save()
            if not items:
                messages.error(request, "Keep at least one item on the order.")
                return redirect(request.path)
            messages.success(request, "Order updated.")
            return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")
    else:
        form = BuyRequestForm(instance=req)
        formset = ItemFormSet(instance=req)
    return render(request, "trips/request_form.html",
                  {"plan": plan, "form": form, "formset": formset, "is_edit": True, "req": req})


@profile_required
@require_POST
def request_cancel(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if req.buyer_id != request.user.id:
        messages.error(request, "You can only cancel your own order.")
        return redirect(reverse("accounts:profile") + "#my-orders")
    if not req.can_edit:
        messages.error(request, "This order can no longer be cancelled.")
        return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")
    req.status = Status.CANCELLED
    req.save(update_fields=["status", "updated_at"])
    messages.success(request, "Order cancelled.")
    return redirect(reverse("accounts:profile") + "#my-orders")


# --- Buyer-first: post an order with no traveler yet ------------------------
def _resolve_proxy(proxy_id):
    """Return the active ProxyBuyer for the given id, or None."""
    if not proxy_id:
        return None
    return ProxyBuyer.objects.filter(pk=proxy_id, is_active=True).first()


@profile_required
def order_create(request):
    # Flow-1 entry: the buyer picked a proxy on the home "Proxy Buyers" list, so
    # this is always a Products order with the origin country fixed by the proxy.
    proxy = _resolve_proxy(request.GET.get("proxy") or request.POST.get("proxy"))
    if request.method == "POST":
        # Cargo lists items WITH a declared unit price (customs); Products lists
        # items only (the Proxy Buyer estimates prices later) — same split as the
        # plan-first flow (request_create). A proxy order is always Products.
        cargo = proxy is None and request.POST.get("cargo_only") == "1"
        ItemFormSet = OrderItemFormSet if cargo else RequestItemFormSet
        form = OrderForm(request.POST, proxy=proxy)
        formset = ItemFormSet(request.POST, request.FILES, instance=BuyRequest(), prefix="bf_items")
        if form.is_valid() and formset.is_valid():
            with db_transaction.atomic():
                order = form.save(commit=False)
                order.buyer = request.user
                # cargo_only comes from the form (Products Flow 3 vs Cargo Flow 4).
                if proxy is not None:
                    order.proxy_buyer = proxy
                    order.cargo_only = False
                    order.from_country = proxy.country
                    order.from_city = proxy.city
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
                    if order.cargo_only:
                        # Cargo: the buyer already owns the goods — there is no
                        # traveler "purchase" step, so the declared qty/price
                        # stand as final for the customs invoice. (Products
                        # orders stay unpurchased until the traveler buys them.)
                        item.actual_quantity = item.quantity
                        item.actual_unit_cost = item.estimated_unit_cost
                        item.purchased_at = timezone.now()
                    item.save()
            if proxy is not None:
                workflow.on_proxy_order_created(order)
                messages.success(request, "Order Posted. Proxy Buyer will soon respond with estimate.")
            else:
                messages.success(request, "Order posted. Travelers can now respond with offers.")
            return redirect(reverse("accounts:profile") + f"?order={order.id}#order-detail")
    else:
        form = OrderForm(proxy=proxy)
        formset = OrderItemFormSet(instance=BuyRequest(), prefix="bf_items")
    from apps.accounts.views import _resolve_role
    return render(request, "trips/order_form.html",
                  {"form": form, "formset": formset, "proxy": proxy,
                   "role": _resolve_role(request)})


# --- Buyer: edit / cancel a buyer-first order before any offer -------------
@profile_required
def order_edit(request, pk):
    order = get_object_or_404(BuyRequest, pk=pk, plan__isnull=True)
    if order.buyer_id != request.user.id:
        messages.error(request, "You can only edit your own order.")
        return redirect(reverse("accounts:profile") + "#my-orders")
    if not order.can_edit:
        messages.error(request, "This order can no longer be edited.")
        return redirect(reverse("accounts:profile") + f"?order={order.id}#order-detail")
    # A proxy (Flow-1) order keeps its proxy + Products typing on edit.
    proxy = order.proxy_buyer
    if request.method == "POST":
        cargo = proxy is None and request.POST.get("cargo_only") == "1"
        ItemFormSet = OrderItemFormSet if cargo else RequestItemFormSet
        form = OrderForm(request.POST, instance=order, proxy=proxy)
        formset = ItemFormSet(request.POST, request.FILES, instance=order, prefix="bf_items")
        if form.is_valid() and formset.is_valid():
            with db_transaction.atomic():
                order = form.save(commit=False)
                if proxy is not None:
                    order.cargo_only = False
                    order.from_country = proxy.country
                    order.from_city = proxy.city
                order.settlement_currency = ExchangeRate.currency_for_country(order.from_country)
                order.save()
                formset.save()
                items = list(order.items.order_by("position", "id"))
                if not items:
                    db_transaction.set_rollback(True)
                else:
                    for idx, item in enumerate(items, start=1):
                        item.position = idx
                        if order.cargo_only:
                            item.actual_quantity = item.quantity
                            item.actual_unit_cost = item.estimated_unit_cost
                            if not item.purchased_at:
                                item.purchased_at = timezone.now()
                        else:
                            # Switched to Products before any offer: drop any
                            # stale "purchased" data so the traveler buys later.
                            item.actual_quantity = 0
                            item.actual_unit_cost = Decimal("0")
                            item.purchased_at = None
                        item.save()
            if not items:
                messages.error(request, "Keep at least one item on the order.")
                return redirect(request.path)
            messages.success(request, "Order updated.")
            return redirect(reverse("accounts:profile") + f"?order={order.id}#order-detail")
    else:
        form = OrderForm(instance=order, proxy=proxy)
        formset = OrderItemFormSet(instance=order, prefix="bf_items")
    from apps.accounts.views import _resolve_role
    return render(request, "trips/order_form.html",
                  {"form": form, "formset": formset, "is_edit": True, "order": order,
                   "proxy": proxy, "role": _resolve_role(request)})


@profile_required
@require_POST
def order_cancel(request, pk):
    order = get_object_or_404(BuyRequest, pk=pk, plan__isnull=True)
    if order.buyer_id != request.user.id:
        messages.error(request, "You can only cancel your own order.")
        return redirect(reverse("accounts:profile") + "#my-orders")
    if not order.can_edit:
        messages.error(request, "This order can no longer be cancelled.")
        return redirect(reverse("accounts:profile") + f"?order={order.id}#order-detail")
    order.status = Status.CANCELLED
    order.save(update_fields=["status", "updated_at"])
    messages.success(request, "Order cancelled.")
    return redirect(reverse("accounts:profile") + "#my-orders")


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
    if not flow_types.order_accepts_carry_offer(order):
        messages.error(request, flow_types.PRODUCTS_ORDER_NEEDS_PROXY)
        return redirect(dashboard_url + "#travel-plans")
    # Flow-1 Products: one traveler per order (FCFS) — block if anyone already
    # holds a live offer. (Cargo orders may collect several travelers' offers.)
    if not order.is_cargo and order.traveler_offers.filter(
        offer_status__in=[OfferStatus.PENDING, OfferStatus.SELECTED]
    ).exists():
        messages.info(request, "Another traveler is already handling this cargo.")
        return redirect(dashboard_url + "#travel-plans")

    form = (TravelerOfferForm if order.is_cargo else TravelerCargoOfferForm)(request.POST)
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


# --- Proxy Buyer: respond to a Products (buyer-first) order with an estimate -
# The proxy fills per-item unit costs + estimated weight + their rate (the
# "Estimated Cost" form). First-come-first-served: a Products order takes a
# single proxy and is locked until they withdraw.
@profile_required
@require_POST
def offer_estimate_create(request, order_id):
    dashboard_url = reverse("accounts:profile")
    order = get_object_or_404(BuyRequest, pk=order_id, plan__isnull=True)
    if order.buyer_id == request.user.id:
        messages.error(request, "You cannot place an offer on your own order.")
        return redirect(reverse("pages:home") + "#open-orders")
    if order.is_cargo:
        messages.error(request, "This is a Cargo order — use the carry offer form.")
        return redirect(dashboard_url + "#travel-plans")
    if order.status not in OPEN_ORDER_STATUSES:
        messages.info(request, "This order is no longer accepting offers.")
        return redirect(dashboard_url + "#travel-plans")
    # FCFS lock: a Products order takes one proxy. Block if anyone already holds
    # a live (pending or selected) offer on it.
    if order.traveler_offers.filter(
        offer_status__in=[OfferStatus.PENDING, OfferStatus.SELECTED]
    ).exists():
        messages.info(request, "Another Proxy Buyer is already handling this order.")
        return redirect(dashboard_url + "#travel-plans")

    form = ProxyOfferForm(request.POST)
    review_form = ReviewForm(request.POST, instance=order)
    review_formset = ReviewItemFormSet(request.POST, instance=order, prefix="est_items")
    if form.is_valid() and review_form.is_valid() and review_formset.is_valid():
        if (review_form.cleaned_data.get("estimated_weight_kg") or 0) <= 0:
            messages.error(request, "Enter the estimated package weight (kg).")
            return redirect(dashboard_url + f"?offer={order_id}#offer-form")
        with db_transaction.atomic():
            review_formset.save()            # estimated_unit_cost on each item
            review_form.save()               # order.estimated_weight_kg
            offer = form.save(commit=False)
            offer.order = order
            offer.traveler = request.user
            offer.pickup_address = request.user.traveler_address
            offer.avail_kg = order.estimated_weight_kg or Decimal("0")
            offer.save()
            order.recompute_status()
        workflow.on_offer_submitted(order, offer)
        messages.success(request, "Estimate submitted. The buyer will review it.")
        return redirect(dashboard_url + "#travel-plans")
    for f in (form, review_form):
        for field, errs in f.errors.items():
            for err in errs:
                messages.error(request, f"{field}: {err}")
    for err in review_formset.non_form_errors():
        messages.error(request, err)
    return redirect(dashboard_url + f"?offer={order_id}#offer-form")


# --- Proxy buyer (Flow-1): send the estimate on an assigned order ------------
@profile_required
@require_POST
def proxy_estimate(request, order_id):
    dashboard = reverse("accounts:profile")
    order = get_object_or_404(BuyRequest, pk=order_id, plan__isnull=True)
    panel = dashboard + f"?estimate={order.id}#estimate-form"
    if not (order.proxy_buyer_id and order.proxy_buyer.user_id == request.user.id):
        messages.error(request, "Only the assigned Proxy Buyer can send an estimate.")
        return redirect(dashboard + "#proxy-orders")
    if order.is_cargo:
        messages.error(request, "This is a Cargo order — no proxy estimate applies.")
        return redirect(dashboard + "#proxy-orders")
    if order.status != Status.OPEN:
        messages.info(request, "You have already sent an estimate for this order.")
        return redirect(dashboard + f"?order={order.id}#order-detail")

    form = ProxyEstimateForm(request.POST, instance=order)
    formset = ReviewItemFormSet(request.POST, instance=order, prefix="est_items")
    if form.is_valid() and formset.is_valid():
        with db_transaction.atomic():
            formset.save()                 # estimated_unit_cost on each item
            order = form.save(commit=False)  # estimated_weight_kg + proxy_margin_percent
            order.status = Status.RESPONDED
            order.save()
        messages.success(request, "Estimate sent. Your order is now looking for a traveler.")
        return redirect(dashboard + f"?order={order.id}#order-detail")
    for field, errs in form.errors.items():
        for err in errs:
            messages.error(request, f"{field}: {err}")
    for err in formset.non_form_errors():
        messages.error(request, err)
    return redirect(panel)


# --- Proxy buyer: record the purchase (actual costs) -> Package Ready ---------
@profile_required
@require_POST
def proxy_purchase(request, order_id):
    order = get_object_or_404(BuyRequest, pk=order_id, plan__isnull=True)
    dash = reverse("accounts:profile")
    detail = dash + f"?order={order.id}#order-detail"
    if not (order.proxy_buyer_id and order.proxy_buyer.user_id == request.user.id
            and not order.is_cargo):
        messages.error(request, "Only the assigned Proxy Buyer can record the purchase.")
        return redirect(dash + "#proxy-orders")
    if not order.proxy_actuals_editable:
        messages.info(request, "Actual costs can't be changed at this stage.")
        return redirect(detail)
    formset = PurchaseItemFormSet(request.POST, request.FILES, instance=order)
    if formset.is_valid():
        items = formset.save(commit=False)
        for item in items:
            if item.actual_unit_cost and not item.purchased_at:
                item.purchased_at = timezone.now()
            item.save()
        workflow.on_items_purchased(order)
        messages.success(request, "Package marked ready. The buyer will pay the remaining balance.")
        return redirect(detail)
    for err in formset.non_form_errors():
        messages.error(request, err)
    return redirect(dash + f"?package_ready={order.id}#package-ready")


# --- Buyer: accept / reject the traveler's shipment cost (Flow-1 step 7-8) ---
@profile_required
@require_POST
def order_accept(request, order_id):
    order = get_object_or_404(BuyRequest, pk=order_id, plan__isnull=True)
    detail = reverse("accounts:profile") + f"?order={order.id}#order-detail"
    if request.user != order.buyer:
        messages.error(request, "Only the buyer can accept the shipment cost.")
        return redirect(detail)
    if order.is_cargo or order.status != Status.RESPONDED:
        messages.error(request, "There is no shipment cost to accept at this stage.")
        return redirect(detail)
    offer = order.traveler_offers.filter(offer_status=OfferStatus.PENDING).first()
    if not offer:
        messages.error(request, "No traveler offer to accept.")
        return redirect(detail)
    order.status = Status.ACCEPTED
    order.save(update_fields=["status", "updated_at"])
    workflow.on_proxy_offer_accepted(order, offer)
    messages.success(request, "Shipment cost accepted. Please pay the deposit to confirm.")
    return redirect(detail)


@profile_required
@require_POST
def order_reject(request, order_id):
    order = get_object_or_404(BuyRequest, pk=order_id, plan__isnull=True)
    detail = reverse("accounts:profile") + f"?order={order.id}#order-detail"
    if request.user != order.buyer:
        messages.error(request, "Only the buyer can reject the shipment cost.")
        return redirect(detail)
    if order.is_cargo or order.status != Status.RESPONDED:
        messages.error(request, "There is no offer to reject at this stage.")
        return redirect(detail)
    offer = order.traveler_offers.filter(offer_status=OfferStatus.PENDING).first()
    if not offer:
        messages.error(request, "No traveler offer to reject.")
        return redirect(detail)
    offer.offer_status = OfferStatus.REJECTED
    offer.save(update_fields=["offer_status", "updated_at"])
    workflow.on_proxy_offer_rejected(order, offer)
    messages.success(request, "Offer declined. Your order is open for other travelers again.")
    return redirect(detail)


# --- Buyer: pay the deposit on a Products order (against the proxy's estimate) -
@profile_required
@require_POST
def order_deposit_pay(request, order_id):
    order = get_object_or_404(BuyRequest, pk=order_id, plan__isnull=True)
    detail = reverse("accounts:profile") + f"?order={order.id}#order-detail"
    if request.user != order.buyer:
        messages.error(request, "Only the buyer can submit the deposit.")
        return redirect(detail)
    if order.is_cargo:
        messages.error(request, "This is a Cargo order — use the cargo deposit flow.")
        return redirect(detail)
    # The deposit is due once the buyer has accepted the traveler's shipment cost
    # (status ACCEPTED). Once verified, the order advances to DEPOSIT_PAID, so
    # ACCEPTED also covers re-uploading proof before verification.
    if order.status != Status.ACCEPTED:
        messages.error(request, "No deposit is due at this stage.")
        return redirect(detail)
    tx, _ = Transaction.objects.get_or_create(request=order)
    # Recorded in the order's invoice currency to keep the balance math
    # consistent (invoice_total − payments are all one currency). The buyer is
    # instructed to settle the IDR equivalent; admin reconciles via that.
    Payment.objects.create(
        transaction=tx,
        direction=Payment.Direction.INBOUND,
        kind=Payment.Kind.DEPOSIT,
        currency=order.currency,
        amount=order.deposit_due,
        proof=request.FILES.get("proof"),
        note=request.POST.get("note", ""),
    )
    messages.success(request, "Deposit proof submitted. Admin will verify it shortly.")
    return redirect(detail)


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


# --- Traveler: edit a pending offer (before the buyer selects it) -----------
@profile_required
def offer_edit(request, pk):
    offer = get_object_or_404(TravelerOffer.objects.select_related("order"), pk=pk)
    if offer.traveler_id != request.user.id:
        messages.error(request, "You can only edit your own offer.")
        return redirect(reverse("accounts:profile") + "#travel-plans")
    if not offer.can_edit:
        messages.error(request, "This offer can no longer be edited.")
        return redirect(reverse("accounts:profile") + f"?offer={offer.id}#offer-detail")
    if request.method == "POST":
        form = TravelerOfferForm(request.POST, instance=offer)
        if form.is_valid():
            edited = form.save(commit=False)
            edited.pickup_address = request.user.traveler_address
            edited.save()
            offer.order.recompute_status()
            messages.success(request, "Offer updated.")
            return redirect(reverse("accounts:profile") + f"?offer={offer.id}#offer-detail")
        for field, errs in form.errors.items():
            for err in errs:
                messages.error(request, f"{field}: {err}")
    else:
        form = TravelerOfferForm(instance=offer)
    return render(request, "trips/offer_edit.html", {"form": form, "offer": offer})


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


# --- Buyer: split the cargo goods across carriers (per-leg customs) ----------
@profile_required
@require_POST
def order_assign_items(request, order_id):
    order = get_object_or_404(BuyRequest, pk=order_id, plan__isnull=True, cargo_only=True)
    detail = reverse("accounts:profile") + f"?order={order.id}#order-detail"
    if order.buyer_id != request.user.id:
        messages.error(request, "Only the buyer can assign the goods.")
        return redirect(detail)
    legs = order.confirmed_legs
    if len(legs) < 2:
        messages.info(request, "Splitting goods is only needed when more than one carrier is selected.")
        return redirect(detail)

    over = []
    with db_transaction.atomic():
        for item in order.items.all():
            total = 0
            for leg in legs:
                try:
                    qty = max(int(request.POST.get(f"alloc_{item.id}_{leg.id}", "0") or "0"), 0)
                except ValueError:
                    qty = 0
                total += qty
                ItemLegAllocation.objects.update_or_create(
                    item=item, leg=leg, defaults={"quantity": qty},
                )
            if total > item.quantity:
                over.append(f"{item.name} ({total}/{item.quantity})")
        if over:
            db_transaction.set_rollback(True)

    if over:
        messages.error(request, "Assigned more than available for: " + ", ".join(over) + ". Nothing saved.")
    elif order.items_fully_assigned:
        messages.success(request, "Goods split across carriers — each carrier's customs invoice is ready.")
    else:
        messages.success(request, "Saved. Some units are still unassigned — assign them all before drop-off.")
    return redirect(detail)


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
    # The traveler records the drop-off and the final weight in one step,
    # done in front of the buyer. Merges drop-off + weighing + receipt.
    offer = get_object_or_404(
        TravelerOffer, pk=pk, traveler=request.user,
        offer_status=OfferStatus.SELECTED, leg_status__isnull=True,
    )
    detail_url = reverse("accounts:profile") + f"?offer={offer.id}#offer-detail"
    if not offer.deposit_verified:
        messages.error(request, "The buyer's deposit must clear before drop-off.")
        return redirect(detail_url)
    # Multi-carrier cargo: the buyer must split the goods first, so they hand you
    # the right items at drop-off (and your customs invoice is ready).
    if offer.order.is_multi_leg_cargo and not offer.order.items_fully_assigned:
        messages.error(request, "The buyer hasn't split the goods across carriers yet — drop-off can't be recorded until they do.")
        return redirect(detail_url)
    try:
        weight = Decimal(request.POST.get("agreed_weight_kg", "0"))
    except InvalidOperation:
        weight = Decimal("0")
    if weight <= 0:
        messages.error(request, "Enter a valid final weight.")
        return redirect(detail_url)

    now = timezone.now()
    offer.agreed_weight_kg = weight
    offer.leg_status = LegStatus.PACKAGE_RECEIVED
    offer.dropped_off_at = now
    offer.weight_verified_at = now
    offer.received_at = now
    offer.save(update_fields=[
        "agreed_weight_kg", "leg_status", "dropped_off_at",
        "weight_verified_at", "received_at", "updated_at",
    ])
    offer.order.recompute_status()
    messages.success(request, f"Drop-off recorded — final weight {weight} kg. This is final and not subject to dispute.")
    return redirect(detail_url)


# --- Traveler: confirm custody of a weight-verified leg ------------------------
@profile_required
@require_POST
def leg_received(request, pk):
    offer = get_object_or_404(
        TravelerOffer, pk=pk, traveler=request.user, leg_status=LegStatus.WEIGHT_VERIFIED
    )
    detail_url = reverse("accounts:profile") + f"?offer={offer.id}#offer-detail"
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
    detail_url = reverse("accounts:profile") + f"?offer={offer.id}#offer-detail"
    # Traveler records any customs duty paid at the destination (reimbursable).
    form = LegCustomFareForm(request.POST, request.FILES, instance=offer)
    if not form.is_valid():
        for errs in form.errors.values():
            for e in errs:
                messages.error(request, e)
        return redirect(detail_url)
    form.save()
    offer.leg_status = LegStatus.PACKAGE_ARRIVED
    offer.arrived_at = timezone.now()
    offer.save(update_fields=["leg_status", "arrived_at", "updated_at"])
    offer.order.recompute_status()
    messages.success(
        request,
        "Marked as arrived. The buyer will reimburse any customs duty and settle the balance, then choose pickup or reship.",
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
        # Buyer met the traveler and verified the package — release the payment.
        offer.fulfillment_method = FulfillmentMethod.PICKUP
        offer.leg_status = LegStatus.CLEAR
        offer.cleared_at = timezone.now()
        offer.save(update_fields=["fulfillment_method", "leg_status", "cleared_at", "updated_at"])
        order.recompute_status()
        messages.success(request, "Pickup confirmed — this leg will close and the traveler will be paid shortly.")
    elif choice == FulfillmentMethod.RESHIP:
        address = (order.buyer.buyer_invoice_address or "").strip()
        if not address:
            messages.error(request, "Please set your Reshipment Address on your Profile page first.")
            return redirect(detail_url)
        offer.fulfillment_method = FulfillmentMethod.RESHIP
        offer.reshipment_address = address
        offer.leg_status = LegStatus.RESHIP_REQUESTED
        offer.save(update_fields=["fulfillment_method", "reshipment_address", "leg_status", "updated_at"])
        order.recompute_status()
        _notify_leg_reship_requested(offer)
        messages.success(request, "Reshipment requested. The traveler has been notified by email and WhatsApp.")
    else:
        messages.error(request, "Choose Pickup or Reship.")
    return redirect(detail_url)


def _notify_leg_reship_requested(offer):
    """Email + WhatsApp the leg's traveler that the buyer requested reshipment.
    Best-effort: a notification failure must not block the state transition."""
    from types import SimpleNamespace

    order = offer.order
    traveler = offer.traveler
    try:
        send_whatsapp(
            to_user=traveler,
            text=(
                f"Buyer requested reshipment for {order.reference}. "
                f"Please log in to send the shipping cost and your bank details."
            ),
            event="leg_reship_requested",
        )
    except Exception:
        pass
    try:
        send_email(
            to_user=traveler,
            subject=f"Buyer requested reshipment — {order.reference}",
            template="reship_requested",
            context={
                "request_obj": SimpleNamespace(
                    reference=order.reference, reshipment_address=offer.reshipment_address
                ),
                "request_url": reverse("accounts:profile") + f"?offer={offer.id}#offer-detail",
            },
            event="leg_reship_requested",
        )
    except Exception:
        pass


# --- Traveler: send a leg's reshipment cost + bank details --------------------
@profile_required
@require_POST
def leg_reship_cost(request, pk):
    offer = get_object_or_404(
        TravelerOffer, pk=pk, traveler=request.user, leg_status=LegStatus.RESHIP_REQUESTED
    )
    detail_url = reverse("accounts:profile") + f"?offer={offer.id}#offer-detail"
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
    detail_url = reverse("accounts:profile") + f"?offer={offer.id}#offer-detail"
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
    req = get_object_or_404(
        BuyRequest.objects.select_related("plan", "plan__traveler", "buyer", "proxy_buyer__user"), pk=pk
    )
    if not (request.user.is_staff or req.is_chat_participant(request.user)):
        messages.error(request, "You cannot post to this conversation.")
        return redirect("pages:home")
    # Return the poster to the panel they were on: the traveler's offer detail for
    # a buyer-first order, else the buyer/proxy order detail.
    is_buyer_or_proxy = request.user.id == req.buyer_id or (
        req.proxy_buyer_id and req.proxy_buyer.user_id == request.user.id)
    if req.plan_id is None and not is_buyer_or_proxy and not request.user.is_staff:
        back = _traveler_invoice_redirect(req)
    else:
        back = redirect(req.get_absolute_url())
    if req.status not in CHAT_STATUSES and not request.user.is_staff:
        messages.error(request, "Chat is not available at this stage.")
        return back
    form = MessageForm(request.POST, request.FILES)
    if form.is_valid():
        msg = form.save(commit=False)
        msg.request = req
        msg.sender = request.user
        msg.save()
        workflow.on_new_message(msg)
    else:
        messages.error(request, "Message cannot be empty.")
    return back


def _require_traveler(request, req):
    if req.plan_id:
        return request.user == req.plan.traveler
    # Buyer-first (Products FCFS): the proxy is the single live offer's traveler.
    return request.user == workflow._order_traveler(req)


def _traveler_invoice_redirect(req):
    """Send the traveler back to the page where they see the invoice after an
    action: their offer detail for a buyer-first order, the order detail (their
    own request panel) for plan-first."""
    if not req.plan_id:
        offer = req.traveler_offers.filter(
            offer_status__in=[OfferStatus.PENDING, OfferStatus.SELECTED]
        ).first()
        if offer:
            return redirect(reverse("accounts:profile") + f"?offer={offer.id}#offer-detail")
    return redirect(reverse("accounts:profile") + f"?order={req.id}#order-detail")


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
                        if req.is_cargo:
                            messages.success(request, "Acceptance sent. The buyer has been notified.")
                        else:
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
            return _traveler_invoice_redirect(req)
    else:
        form = PurchaseWeightForm(instance=req)
        formset = PurchaseItemFormSet(instance=req)
    return render(request, "trips/request_purchase.html", {"req": req, "form": form, "formset": formset})


# --- Traveler: confirm receipt + final weight -> Package Received -----------
# Cargo: the traveler receives the package from the BUYER (after the deposit).
# Flow-1 proxy: the traveler receives the goods from the PROXY (after the buyer
# has paid the package-ready balance) — this is the handover that releases the
# proxy's first 50%.
@profile_required
@require_POST
def request_receive(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if not _require_traveler(request, req):
        messages.error(request, "Only the traveler can confirm receipt.")
        return redirect(req.get_absolute_url())
    if req.is_cargo:
        receivable, on_received = Status.DEPOSIT_PAID, workflow.on_cargo_package_received
    elif req.is_proxy_buyer_first:
        receivable, on_received = Status.ITEMS_PURCHASED, workflow.on_proxy_package_received
    else:
        messages.info(request, "Receipt confirmation does not apply to this order.")
        return redirect(req.get_absolute_url())
    if req.status != receivable:
        messages.info(request, "You can confirm receipt once the package is ready.")
        return redirect(req.get_absolute_url())

    form = PurchaseWeightForm(request.POST, instance=req)
    if form.is_valid():
        weight = form.cleaned_data.get("actual_weight_kg") or Decimal("0")
        if weight <= 0:
            messages.error(request, "Enter the measured package weight (kg) before confirming receipt.")
            return redirect(reverse("accounts:profile") + f"?receive={req.id}#receive-order")
        form.save()
        on_received(req)
        messages.success(request, "Package received — custody confirmed. The buyer has been notified.")
        return _traveler_invoice_redirect(req)
    messages.error(request, "Enter a valid measured weight (kg).")
    return redirect(reverse("accounts:profile") + f"?receive={req.id}#receive-order")


# --- Traveler: arrival + custom fare -> Package Arrived ---------------------
@profile_required
def request_arrive(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if not _require_traveler(request, req):
        messages.error(request, "Only the traveler can update arrival.")
        return redirect(req.get_absolute_url())
    # Cargo (Carrier) has no purchase step — it arrives straight from Package
    # Received. Flow-1 proxy arrives after the handover (Package Received) too.
    # Plan-first proxy buying (traveler IS the proxy) arrives from Items Purchased.
    waits_for_handover = req.is_cargo or req.is_proxy_buyer_first
    arrivable = Status.PACKAGE_RECEIVED if waits_for_handover else Status.ITEMS_PURCHASED
    if req.status != arrivable:
        messages.info(
            request,
            "You can mark arrival after the package is received."
            if waits_for_handover else "You can mark arrival after items are purchased.",
        )
        return redirect(req.get_absolute_url())

    if request.method == "POST":
        form = CustomFareForm(request.POST, request.FILES, instance=req)
        if form.is_valid():
            form.save()
            if req.is_cargo:
                workflow.on_cargo_arrived(req)
                if req.status == Status.READY_FOR_PICKUP:
                    msg = "Marked as arrived. The deposit covers the balance — the buyer can now pick up or reship."
                else:
                    msg = "Marked as arrived. The buyer has been notified to pay the outstanding balance."
            else:
                workflow.on_package_arrived(req)
                msg = "Marked as arrived. The buyer has been notified to pay the balance."
            messages.success(request, msg)
            return _traveler_invoice_redirect(req)
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
        kind, amount = Payment.Kind.BALANCE, req.balance_due_now
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
def request_invoice(request, pk):
    """Full buyer invoice (all financials) as a printable page — the buyer's
    counterpart to the customs invoice. Buyer + staff only."""
    req = get_object_or_404(BuyRequest, pk=pk)
    if not (request.user.is_staff or req.buyer_id == request.user.id):
        messages.error(request, "You don't have access to this invoice.")
        return redirect(reverse("accounts:profile"))
    if not req.items.exists():
        messages.error(request, "This invoice has no items yet.")
        return redirect(req.get_absolute_url())
    return render(request, "trips/order_invoice_print.html", {"req": req})


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
        # Flow-1 proxy order: the carrier is the single live offer's traveler
        # (no leg is created), so let them open the customs invoice too.
        or (not req.plan_id and any(
            o.traveler_id == request.user.id
            and o.offer_status in (OfferStatus.PENDING, OfferStatus.SELECTED)
            for o in req.traveler_offers.all()))
    )
    if not allowed:
        messages.error(request, "You don't have access to this customs invoice.")
        return redirect(reverse("accounts:profile"))
    if not req.customs_invoice_available:
        messages.error(request, "Customs invoice is not available yet.")
        return redirect(req.get_absolute_url())
    # Per-leg customs for multi-carrier cargo: each carrier travels separately and
    # declares only their assigned goods. Resolve which leg to show — an explicit
    # ?leg=, else the requesting traveler's own leg.
    leg = None
    if req.is_cargo and req.is_multi_leg_cargo:
        legs = req.confirmed_legs
        leg_id = request.GET.get("leg")
        if leg_id:
            leg = next((l for l in legs if str(l.id) == str(leg_id)), None)
        if leg is None:
            leg = next((l for l in legs if l.traveler_id == request.user.id), None)
    return render(request, "trips/customs_invoice_print.html", {"req": req, "leg": leg})
