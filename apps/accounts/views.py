from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.notifications.services import send_whatsapp
from apps.trips.constants import CHAT_STATUSES, LegStatus, OfferStatus, Status
from apps.trips.forms import (
    AWBForm, BuyRequestForm, CustomFareForm, MessageForm, PurchaseItemFormSet,
    PurchaseWeightForm, ReshipmentCostForm, RequestItemFormSet, ReviewForm,
    ReviewItemFormSet, TravelPlanForm,
)
from apps.trips.models import BuyRequest, TravelerOffer, TravelPlan

from .forms import ChangePasswordForm, OTPForm, ProfileForm


def _profile_tab(tab):
    """Redirect back to the profile page with a given sidebar tab active."""
    return redirect(reverse("accounts:profile") + f"#{tab}")


@login_required
def profile(request):
    user = request.user

    # Traveler side
    my_plans = TravelPlan.objects.filter(traveler=user).prefetch_related("buy_requests")
    open_plans = [p for p in my_plans if not p.is_closed]
    closed_plans = [p for p in my_plans if p.is_closed]

    # Buyer side — plan-first requests only; buyer-first orders get their own
    # "My Orders" panel below (kept separate — see PLAN-buyer-first-orders.md §7a).
    my_requests = BuyRequest.objects.filter(buyer=user, plan__isnull=False).select_related("plan")
    open_requests = [r for r in my_requests if r.status != Status.CLOSED]
    closed_requests = [r for r in my_requests if r.status == Status.CLOSED]

    # Buyer-first orders the user posted.
    my_orders = BuyRequest.objects.filter(buyer=user, plan__isnull=True).prefetch_related("traveler_offers")
    open_orders = [o for o in my_orders if o.status != Status.CLOSED]
    closed_orders = [o for o in my_orders if o.status == Status.CLOSED]

    # Buyer-first offers the user made as a traveler — own dashboard tab, since
    # the per-leg actions (drop-off, weight verify, package received) are
    # different from anything on the plan-first "My Travel Plans" tab.
    my_offers = TravelerOffer.objects.filter(traveler=user).select_related("order")
    closed_offer_statuses = {OfferStatus.REJECTED, OfferStatus.WITHDRAWN}
    closed_leg_statuses = {LegStatus.CLOSED, LegStatus.DROPOFF_MISSED}
    open_offers = [
        o for o in my_offers
        if o.offer_status not in closed_offer_statuses and o.leg_status not in closed_leg_statuses
    ]
    closed_offers = [
        o for o in my_offers
        if o.offer_status in closed_offer_statuses or o.leg_status in closed_leg_statuses
    ]

    form = ProfileForm(instance=user)

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
    plan_id = request.GET.get("plan")
    if plan_id:
        plan = (
            TravelPlan.objects.select_related("traveler").filter(pk=plan_id).first()
        )
        if not plan or not (user == plan.traveler or user.is_staff):
            plan = None

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
            order_form_formset = RequestItemFormSet(instance=BuyRequest())

    return render(
        request,
        "accounts/profile.html",
        {
            "profile_form": form,
            "otp_form": OTPForm(),
            "password_form": ChangePasswordForm(user),
            "plan_form": TravelPlanForm(),
            "open_plans": open_plans,
            "closed_plans": closed_plans,
            "open_requests": open_requests,
            "closed_requests": closed_requests,
            "open_orders": open_orders,
            "closed_orders": closed_orders,
            "open_offers": open_offers,
            "closed_offers": closed_offers,
            "block_plan_id": request.GET.get("block"),
            "order": order,
            "plan": plan,
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
            **order_ctx,
        },
    )


@login_required
@require_POST
def profile_update(request):
    form = ProfileForm(request.POST, instance=request.user)
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
        text=f"Your Jastip.me verification code is {code}. It expires in 10 minutes.",
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
