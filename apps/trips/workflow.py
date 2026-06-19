"""Status transitions + the email/WhatsApp notifications that accompany them.

Keeping the side effects here keeps views and admin actions thin and ensures
every lifecycle step sends the right email (cc admin) + WhatsApp reminder.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

from apps.notifications.services import notify_see_email, send_email, send_whatsapp

from .constants import Status

logger = logging.getLogger(__name__)


def _site_url(path: str = "") -> str:
    domain = getattr(settings, "SITE_DOMAIN", "localhost:8019")
    scheme = "http" if settings.DEBUG else "https"
    return f"{scheme}://{domain}{path}"


def _static_url(path: str) -> str:
    """Absolute URL for a static asset, using the plain (non-hashed) path so
    email rendering never depends on the staticfiles manifest being current."""
    base = settings.STATIC_URL if settings.STATIC_URL.startswith("/") else "/" + settings.STATIC_URL
    return _site_url(base + path)


def _ctx(request_obj, **extra):
    ctx = {
        "request_obj": request_obj,
        "plan": request_obj.plan,
        "buyer": request_obj.buyer,
        "traveler": request_obj.plan.traveler,
        "request_url": _site_url(request_obj.get_absolute_url()),
        "site_url": _site_url("/"),
        "logo_url": _static_url("img/logo-email.png"),
        "PAYMENT_DEADLINE_HOURS": getattr(settings, "PAYMENT_DEADLINE_HOURS", 24),
        "BANK": getattr(settings, "BANK_DETAILS", {}),
    }
    ctx.update(extra)
    return ctx


def _set_status(request_obj, status, *, sync_plan=True):
    request_obj.status = status
    request_obj.save(update_fields=["status", "updated_at"])
    if sync_plan:
        request_obj.plan.status = status
        request_obj.plan.save(update_fields=["status", "updated_at"])


# --- Lifecycle steps --------------------------------------------------------

def on_request_submitted(request_obj):
    """Step 2: buyer submitted a request -> notify traveler.
    Plan status is not touched — multiple buyers can be at different stages."""
    _set_status(request_obj, Status.REQUEST_RECEIVED, sync_plan=False)
    subject = (
        "New cargo carrying request for your trip"
        if request_obj.is_cargo
        else "New buying request for your trip"
    )
    send_email(
        to_user=request_obj.plan.traveler,
        subject=subject,
        template="request_received",
        context=_ctx(request_obj),
        event="request_received",
    )
    notify_see_email(request_obj.plan.traveler, event="request_received")


def on_request_accepted(request_obj):
    """Step 3 (accept): traveler priced + accepted -> notify buyer with a PDF
    invoice attached (also cc'd to the traveler + admin).
    Plan status is not touched — multiple buyers can be at different stages."""
    _set_status(request_obj, Status.ACCEPTED, sync_plan=False)
    attachments = None
    # The customs-invoice PDF is for the traveler to print and show at customs —
    # not for the buyer. So cargo acceptance emails carry no attachment; the
    # traveler prints it from the request page instead.
    if not request_obj.is_cargo:
        try:
            from .invoices import render_invoice_pdf
            pdf = render_invoice_pdf(request_obj)
            attachments = [(f"invoice-{request_obj.reference}.pdf", pdf, "application/pdf")]
        except Exception:  # pragma: no cover - never block the email on a PDF error
            logger.exception("Invoice PDF generation failed for %s", request_obj.reference)
    send_email(
        to_user=request_obj.buyer,
        subject="Your request was accepted — please transfer the deposit",
        template="request_accepted",
        context=_ctx(request_obj),
        event="request_accepted",
        attachments=attachments,
    )
    notify_see_email(request_obj.buyer, event="request_accepted")


def on_deposit_cancelled(request_obj):
    """Auto-cancellation: buyer did not pay the deposit within the deadline.
    Plan status is not touched — remaining_weight_kg recovers automatically."""
    request_obj.status = Status.CANCELLED
    request_obj.save(update_fields=["status", "updated_at"])
    send_email(
        to_user=request_obj.buyer,
        subject="Your order has been cancelled",
        template="deposit_cancelled_buyer",
        context=_ctx(request_obj),
        event="deposit_cancelled",
    )
    notify_see_email(request_obj.buyer, event="deposit_cancelled")
    send_email(
        to_user=request_obj.plan.traveler,
        subject=f"Order {request_obj.reference} cancelled — buyer did not pay",
        template="deposit_cancelled_traveler",
        context=_ctx(request_obj),
        event="deposit_cancelled",
    )
    notify_see_email(request_obj.plan.traveler, event="deposit_cancelled")


def on_request_rejected(request_obj, reason=""):
    """Step 3 (reject): traveler rejected -> notify buyer.
    Plan status is not changed — remaining_weight_kg automatically recovers
    because rejected requests are excluded from utilized_weight_kg."""
    request_obj.rejection_reason = reason
    request_obj.status = Status.REJECTED
    request_obj.save(update_fields=["status", "rejection_reason", "updated_at"])
    send_email(
        to_user=request_obj.buyer,
        subject="Update on your buying request",
        template="request_rejected",
        context=_ctx(request_obj, reason=reason),
        event="request_rejected",
    )
    notify_see_email(request_obj.buyer, event="request_rejected")


def on_deposit_verified(request_obj):
    """Step 4: admin verified the deposit -> funds forwarded to traveler."""
    _set_status(request_obj, Status.DEPOSIT_PAID)
    send_email(
        to_user=request_obj.plan.traveler,
        subject="Deposit received — you can start purchasing",
        template="deposit_paid",
        context=_ctx(request_obj),
        event="deposit_paid",
    )
    notify_see_email(request_obj.plan.traveler, event="deposit_paid")


def on_cargo_package_received(request_obj):
    """Cargo (Carrier) Phase 2b — step 1: the traveler received the package from
    the buyer and recorded the final measured weight. Notify the buyer; the
    carrier plan's status is untouched (multiple buyers, independent stages)."""
    _set_status(request_obj, Status.PACKAGE_RECEIVED, sync_plan=False)
    send_email(
        to_user=request_obj.buyer,
        subject="Your package was received by the traveler",
        template="cargo_package_received",
        context=_ctx(request_obj),
        event="cargo_package_received",
    )
    notify_see_email(request_obj.buyer, event="cargo_package_received")


def on_items_purchased(request_obj):
    """Step 5: traveler recorded purchases -> invoice ready, notify buyer + send customs invoice to traveler."""
    _set_status(request_obj, Status.ITEMS_PURCHASED)
    send_email(
        to_user=request_obj.buyer,
        subject="Your items have been purchased",
        template="items_purchased",
        context=_ctx(request_obj),
        event="items_purchased",
    )
    notify_see_email(request_obj.buyer, event="items_purchased")
    customs_url = _site_url(f"/trips/requests/{request_obj.pk}/customs-invoice/")
    send_email(
        to_user=request_obj.plan.traveler,
        subject=f"Customs Invoice — {request_obj.reference}",
        template="customs_invoice",
        context=_ctx(request_obj, customs_invoice_url=customs_url),
        event="customs_invoice",
    )


def on_package_arrived(request_obj):
    """Step 6: traveler arrived + paid custom fare -> notify buyer to settle."""
    _set_status(request_obj, Status.PACKAGE_ARRIVED)
    send_email(
        to_user=request_obj.buyer,
        subject="Your package has arrived — please pay the balance",
        template="package_arrived",
        context=_ctx(request_obj),
        event="package_arrived",
    )
    notify_see_email(request_obj.buyer, event="package_arrived")


def on_cargo_arrived(request_obj):
    """Cargo (Carrier) arrival. The traveler recorded any reimbursable customs
    duty. If the buyer still owes a balance (customs + actual-weight top-up over
    the deposit) -> PACKAGE_ARRIVED so they pay it. Otherwise the deposit already
    covers the full carry fee -> straight to READY_FOR_PICKUP (pick up / reship);
    any overpayment is refunded by admin."""
    if request_obj.balance_extra_due > 0:
        on_package_arrived(request_obj)
        return
    _set_status(request_obj, Status.READY_FOR_PICKUP)
    for party in (request_obj.buyer, request_obj.plan.traveler):
        send_email(
            to_user=party,
            subject="Package ready for pickup",
            template="ready_for_pickup",
            context=_ctx(request_obj),
            event="ready_for_pickup",
        )
        notify_see_email(party, event="ready_for_pickup")


def on_balance_verified(request_obj):
    """Step 7: admin verified the balance -> ready for pickup."""
    _set_status(request_obj, Status.READY_FOR_PICKUP)
    for party in (request_obj.buyer, request_obj.plan.traveler):
        send_email(
            to_user=party,
            subject="Package ready for pickup",
            template="ready_for_pickup",
            context=_ctx(request_obj),
            event="ready_for_pickup",
        )
        notify_see_email(party, event="ready_for_pickup")


def on_new_message(message):
    """Notify the other participant(s) of a new chat message (email cc admin +
    WhatsApp ping). Admin posts notify both parties."""
    req = message.request
    participants = [p for p in (req.buyer, req.plan.traveler) if p]
    recipients = [u for u in participants if u != message.sender]
    preview = (message.body or "")[:160]
    for user in recipients:
        send_email(
            to_user=user,
            subject=f"New message on request {req.reference}",
            template="chat_message",
            context=_ctx(req, preview=preview, sender_role=message.role_for(req)),
            event="chat_message",
        )
        send_whatsapp(
            to_user=user,
            text=f"ProxyBuying: 📩 New message on request {req.reference}. Please open the request page to reply.",
            event="chat_message",
        )


def on_reship_requested(request_obj):
    """Buyer submitted delivery address → RESHIP_REQUESTED; traveler notified."""
    _set_status(request_obj, Status.RESHIP_REQUESTED, sync_plan=False)
    send_email(
        to_user=request_obj.plan.traveler,
        subject=f"Buyer requested reshipment — {request_obj.reference}",
        template="reship_requested",
        context=_ctx(request_obj),
        event="reship_requested",
    )
    notify_see_email(request_obj.plan.traveler, event="reship_requested")


def on_reship_cost_sent(request_obj):
    """Traveler submitted cost + bank details → RESHIP_COST_SENT; buyer notified."""
    _set_status(request_obj, Status.RESHIP_COST_SENT, sync_plan=False)
    send_email(
        to_user=request_obj.buyer,
        subject="Reshipment cost details from your traveler",
        template="reship_cost_sent",
        context=_ctx(request_obj),
        event="reship_cost_sent",
    )
    notify_see_email(request_obj.buyer, event="reship_cost_sent")


def on_reship_proof_uploaded(request_obj):
    """Buyer uploaded reshipment payment proof — no status change; traveler notified."""
    send_email(
        to_user=request_obj.plan.traveler,
        subject=f"Buyer uploaded reshipment payment proof — {request_obj.reference}",
        template="reship_proof_uploaded",
        context=_ctx(request_obj),
        event="reship_proof_uploaded",
    )
    notify_see_email(request_obj.plan.traveler, event="reship_proof_uploaded")


def on_reshipped(request_obj):
    """Traveler ships the package → RESHIPPING; buyer notified to confirm receipt."""
    _set_status(request_obj, Status.RESHIPPING, sync_plan=False)
    send_email(
        to_user=request_obj.buyer,
        subject="Your package is on its way",
        template="reshipped",
        context=_ctx(request_obj),
        event="reshipped",
    )
    notify_see_email(request_obj.buyer, event="reshipped")


def on_buyer_cleared(request_obj):
    """Step 8 / CLEAR: buyer picked up the package and confirmed it's good.

    This is the settlement point: the traveler is paid the full invoice less the
    2.5% fee NOW (admin releases funds at Clear). The later move to CLOSED is
    only a display/archival step and carries no payment.
    """
    request_obj.buyer_cleared = True
    request_obj.cleared_at = timezone.now()
    request_obj.save(update_fields=["buyer_cleared", "cleared_at", "updated_at"])
    _set_status(request_obj, Status.CLEAR)
    # Traveler payout notification (full amount less the platform fee).
    send_email(
        to_user=request_obj.plan.traveler,
        subject="Cleared — your full payment is being released",
        template="payout_released",
        context=_ctx(request_obj, payout=request_obj.transaction.payout_to_traveler),
        event="cleared",
    )
    notify_see_email(request_obj.plan.traveler, event="cleared")
    # Buyer thank-you / completion note.
    send_email(
        to_user=request_obj.buyer,
        subject="Thanks — pickup confirmed",
        template="closed",
        context=_ctx(request_obj),
        event="cleared",
    )


def on_cleared(request_obj):
    """Display/archival only: move a CLEAR transaction to CLOSED so it shows in
    the Closed section on the home page. No payment, no emails — the traveler
    was already paid at CLEAR. Triggered by the daily `close_cleared` cron.
    """
    _set_status(request_obj, Status.CLOSED)


def on_offer_submitted(order, offer):
    """Buyer-first flow: traveler submitted an offer → notify the buyer."""
    ctx = {
        "order": order,
        "offer": offer,
        "traveler": offer.traveler,
        "order_url": _site_url(order.get_absolute_url()),
        "site_url": _site_url("/"),
        "logo_url": _static_url("img/logo-email.png"),
    }
    send_email(
        to_user=order.buyer,
        subject=f"New offer on your order {order.reference}",
        template="offer_received",
        context=ctx,
        event="offer_received",
    )
    notify_see_email(order.buyer, event="offer_received")
