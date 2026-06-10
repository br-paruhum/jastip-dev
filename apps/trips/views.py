from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import workflow
from .constants import OPEN_PLAN_STATUSES, Status
from .forms import (
    BuyRequestForm,
    CustomFareForm,
    MessageForm,
    PurchaseItemFormSet,
    RefundBankForm,
    RequestItemFormSet,
    ReviewForm,
    ReviewItemFormSet,
    TravelPlanForm,
)
from .models import BuyRequest, Payment, TravelPlan, Transaction


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
            plan.save()
            messages.success(request, "Travel plan published.")
            return redirect("pages:home")
    else:
        form = TravelPlanForm()
    return render(request, "trips/plan_form.html", {"form": form})


def plan_detail(request, pk):
    plan = get_object_or_404(TravelPlan.objects.select_related("traveler"), pk=pk)
    return render(request, "trips/plan_detail.html", {"plan": plan})


# --- Buyer: block a plan + compose the request ------------------------------
@profile_required
def request_create(request, plan_id):
    plan = get_object_or_404(TravelPlan, pk=plan_id)
    if plan.status not in OPEN_PLAN_STATUSES:
        messages.error(request, "This travel plan is no longer open for requests.")
        return redirect(plan.get_absolute_url())
    if plan.traveler_id == request.user.id:
        messages.error(request, "You cannot block your own travel plan.")
        return redirect(plan.get_absolute_url())

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
            return redirect("accounts:profile")
    else:
        form = BuyRequestForm()
        formset = RequestItemFormSet(instance=BuyRequest())
    return render(request, "trips/request_form.html", {"plan": plan, "form": form, "formset": formset})


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
            "can_chat": is_traveler or is_buyer or request.user.is_staff,
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
    form = MessageForm(request.POST, request.FILES)
    if form.is_valid():
        msg = form.save(commit=False)
        msg.request = req
        msg.sender = request.user
        msg.save()
        workflow.on_new_message(msg)
    else:
        messages.error(request, "Message cannot be empty.")
    return redirect(req.get_absolute_url() + "#chat")


def _require_traveler(request, req):
    return request.user == req.plan.traveler


# --- Traveler: review (price + accept/reject) -------------------------------
@profile_required
def request_review(request, pk):
    req = get_object_or_404(BuyRequest, pk=pk)
    if not _require_traveler(request, req):
        messages.error(request, "Only the traveler can review this request.")
        return redirect(req.get_absolute_url())
    if req.status != Status.REQUEST_RECEIVED:
        messages.info(request, "This request has already been reviewed.")
        return redirect(req.get_absolute_url())

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
                    workflow.on_request_accepted(req)
                    messages.success(request, "Request accepted. The buyer has been notified.")
                    return redirect(req.get_absolute_url())
                elif decision == "reject":
                    workflow.on_request_rejected(req, reason=request.POST.get("rejection_reason", ""))
                    messages.success(request, "Request rejected. The plan is open again.")
                    return redirect(req.get_absolute_url())
                elif decision == "draft":
                    messages.success(request, "Draft saved. Come back to accept or reject anytime.")
                    return redirect("trips:request_review", pk=req.pk)
                else:
                    messages.error(request, "Choose Accept, Reject, or Save Draft.")
    else:
        form = ReviewForm(instance=req)
        formset = ReviewItemFormSet(instance=req)
    return render(request, "trips/request_review.html", {"req": req, "form": form, "formset": formset})


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
        form = ReviewForm(request.POST, instance=req)  # editable estimated weight
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
                return redirect("trips:request_purchase", pk=req.pk)
            workflow.on_items_purchased(req)
            messages.success(request, "Purchases recorded. Invoice updated and buyer notified.")
            return redirect(req.get_absolute_url())
    else:
        form = ReviewForm(instance=req)
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
            return redirect(req.get_absolute_url())
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
    if req.status != Status.READY_FOR_PICKUP:
        messages.error(request, "Clearance is only available once the package is ready for pickup.")
        return redirect(req.get_absolute_url())

    workflow.on_buyer_cleared(req)
    messages.success(
        request,
        "Thank you — marked as Clear. The transaction will close automatically and the "
        "traveler will be paid shortly.",
    )
    return redirect(req.get_absolute_url())
