"""Status transitions + the email/WhatsApp notifications that accompany them.

Keeping the side effects here keeps views and admin actions thin and ensures
every lifecycle step sends the right email (cc admin) + WhatsApp reminder.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

from apps.notifications.services import notify_see_email, send_email, send_whatsapp

from .constants import OfferStatus, Status

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


def _order_traveler(request_obj):
    """The traveler/proxy to notify. Plan-first: the plan's traveler. Buyer-first
    (Products is FCFS single-proxy): the single live offer's traveler."""
    if request_obj.plan_id:
        return request_obj.plan.traveler
    live = [
        o for o in request_obj.traveler_offers.all()
        if o.offer_status in (OfferStatus.PENDING, OfferStatus.SELECTED)
    ]
    return live[0].traveler if len(live) == 1 else None


def _ctx(request_obj, **extra):
    ctx = {
        "request_obj": request_obj,
        "plan": request_obj.plan if request_obj.plan_id else None,
        "buyer": request_obj.buyer,
        "traveler": _order_traveler(request_obj),
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
    # Buyer-first orders have no plan to sync.
    if sync_plan and request_obj.plan_id:
        request_obj.plan.status = status
        request_obj.plan.save(update_fields=["status", "updated_at"])


# --- Lifecycle steps --------------------------------------------------------

def on_proxy_order_created(order):
    """Flow-1 step 1: buyer posted an order and picked a proxy buyer -> notify the
    proxy (email + WhatsApp) so they can send an estimate.

    The proxy is an admin-curated entry that may exist *without* a linked Buyer
    account, so we notify the ProxyBuyer's own admin-set email/WhatsApp fields
    and only fall back to the linked user's contacts when those are blank."""
    _notify_proxy(
        order,
        subject=f"New order to source {order.reference}",
        template="proxy_new_order",
        ctx=_ctx(order),
        event="proxy_new_order",
    )


def _notify_proxy(order, *, subject, template, ctx, event):
    """Notify the order's proxy buyer via their admin-set email/WhatsApp fields,
    falling back to the linked user's contacts when those are blank. Curated
    proxies may exist WITHOUT a linked account, so we don't require one."""
    proxy = order.proxy_buyer if order.proxy_buyer_id else None
    if not proxy:
        return
    proxy_user = proxy.user  # for NotificationLog.recipient (may be None)
    email_to = proxy.email or (proxy_user.email if proxy_user else None)
    # Baileys wants digits only; reuse the model's wa.me digit-stripping.
    wa_to = "".join(ch for ch in proxy.whatsapp if ch.isdigit()) or (
        proxy_user.phone_e164 if proxy_user else None
    )
    if email_to:
        send_email(to_user=proxy_user, to_address=email_to, subject=subject,
                   template=template, context=ctx, event=event)
    if wa_to:
        send_whatsapp(to_user=proxy_user, to_e164=wa_to,
                      text="ProxyBuying: Please see your email.", event=event)


def spawn_spare_baggage_plan(order, offer):
    """Flow-1: a traveler's carry offer is itself a declaration of their trip, so
    advertise their capacity on the home "Looking for Spare Baggage Buyer" board
    as soon as they offer — where OTHER buyers can book the spare via the normal
    plan-first flow ("one traveler, many buyers"). This order's weight is RESERVED
    from the start (prebooked = order weight) so other buyers can't grab the slot
    the original buyer is still deciding on; the reservation is released only if
    the buyer ignores the offer for 24h (see the lapse_ignored_carry_offers cron).
    Idempotent: one offer spawns at most one plan."""
    from decimal import Decimal
    from .models import TravelPlan
    if getattr(offer, "spawned_plan", None) is not None:
        return offer.spawned_plan
    return TravelPlan.objects.create(
        traveler=offer.traveler,
        travel_date=offer.travel_date,
        travel_time=offer.travel_time,
        from_city=offer.from_city, from_country=offer.from_country,
        to_city=offer.to_city, to_country=offer.to_country,
        available_weight_kg=offer.avail_kg,
        prebooked_weight_kg=order.estimated_weight_kg or Decimal("0"),
        shipment_cost_per_kg=offer.ask_cost_per_kg,
        shipment_currency=order.currency,
        carrier_only=True,
        status=Status.NEW,
        origin_offer=offer,
    )


def lock_proxy_cargo_on_plan(order, offer):
    """Flow-1: keep the proxy cargo's reservation on the spare-baggage plan in
    sync with the order's effective weight — the estimate until the proxy records
    the actual weight at purchase, then the actual. Ensures the plan exists."""
    from decimal import Decimal
    plan = getattr(offer, "spawned_plan", None) or spawn_spare_baggage_plan(order, offer)
    plan.prebooked_weight_kg = order.effective_weight_kg or Decimal("0")
    plan.save(update_fields=["prebooked_weight_kg", "updated_at"])
    return plan


def release_offer_reservation(offer):
    """Free the proxy-cargo reservation on this offer's spawned spare-baggage plan
    so the traveler's full capacity becomes bookable again (Avail returns to the
    total). Used when the offer is rejected / withdrawn / lapsed. The plan itself
    stays — it's the traveler's trip declaration."""
    from decimal import Decimal
    plan = getattr(offer, "spawned_plan", None)
    if plan and plan.prebooked_weight_kg:
        plan.prebooked_weight_kg = Decimal("0")
        plan.save(update_fields=["prebooked_weight_kg", "updated_at"])
    return plan


def on_proxy_offer_accepted(order, offer):
    """Flow-1 step 8: buyer accepted the traveler's shipment cost -> notify the
    traveler (prepare to receive the cargo) and the proxy buyer (deposit incoming)."""
    ctx = _ctx(order, offer=offer)
    send_email(
        to_user=offer.traveler,
        subject=f"Your offer on {order.reference} was accepted",
        template="proxy_offer_accepted",
        context=ctx,
        event="offer_accepted",
    )
    notify_see_email(offer.traveler, event="offer_accepted")
    proxy_user = order.proxy_buyer.user if order.proxy_buyer_id else None
    if proxy_user and proxy_user.id != offer.traveler_id:
        send_email(
            to_user=proxy_user,
            subject=f"Traveler confirmed for order {order.reference}",
            template="proxy_offer_accepted",
            context=ctx,
            event="offer_accepted",
        )
        notify_see_email(proxy_user, event="offer_accepted")


def on_proxy_offer_rejected(order, offer):
    """Flow-1: buyer rejected the traveler's shipment cost -> notify the traveler;
    the order returns to the 'Cargo Looking for Traveler' board."""
    send_email(
        to_user=offer.traveler,
        subject=f"Your offer on {order.reference} was declined",
        template="proxy_offer_rejected",
        context=_ctx(order, offer=offer),
        event="offer_rejected",
    )
    notify_see_email(offer.traveler, event="offer_rejected")


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
    _set_status(request_obj, Status.DEPOSIT_PAID, sync_plan=bool(request_obj.plan_id))
    traveler = _order_traveler(request_obj)
    if traveler:
        send_email(
            to_user=traveler,
            subject="Deposit received — you can start purchasing",
            template="deposit_paid",
            context=_ctx(request_obj),
            event="deposit_paid",
        )
        notify_see_email(traveler, event="deposit_paid")


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


def on_proxy_package_received(order):
    """Flow-1 handover: the traveler took custody of the goods from the proxy and
    recorded the actual carried weight. This makes the proxy's FIRST 50% ELIGIBLE
    for disbursement (admin releases the actual money via the admin action; the
    other 50% is held as buyer protection until the buyer clears). Notify buyer."""
    _set_status(order, Status.PACKAGE_RECEIVED, sync_plan=False)
    send_email(
        to_user=order.buyer,
        subject="Your package is on its way",
        template="cargo_package_received",
        context=_ctx(order),
        event="cargo_package_received",
    )
    notify_see_email(order.buyer, event="cargo_package_received")


def on_items_purchased(request_obj):
    """Step 5: traveler recorded purchases -> invoice ready, notify buyer + send customs invoice to traveler."""
    _set_status(request_obj, Status.ITEMS_PURCHASED, sync_plan=bool(request_obj.plan_id))
    send_email(
        to_user=request_obj.buyer,
        subject="Your items have been purchased",
        template="items_purchased",
        context=_ctx(request_obj),
        event="items_purchased",
    )
    notify_see_email(request_obj.buyer, event="items_purchased")
    traveler = _order_traveler(request_obj)
    if traveler:
        customs_url = _site_url(f"/trips/requests/{request_obj.pk}/customs-invoice/")
        # The customs invoice goes out as a PDF attachment — the email itself
        # carries no itemised details (those are in the attached/printable copy).
        attachments = None
        try:
            from .invoices import render_customs_invoice_pdf
            pdf = render_customs_invoice_pdf(request_obj)
            attachments = [(f"customs-invoice-{request_obj.reference}.pdf", pdf, "application/pdf")]
        except Exception:  # pragma: no cover - never block the email on a PDF error
            logger.exception("Customs invoice PDF generation failed for %s", request_obj.reference)
        send_email(
            to_user=traveler,
            subject=f"Customs Invoice — {request_obj.reference}",
            template="customs_invoice",
            context=_ctx(request_obj, customs_invoice_url=customs_url),
            event="customs_invoice",
            attachments=attachments,
        )


def on_package_arrived(request_obj):
    """Step 6: traveler arrived + paid custom fare -> notify buyer to settle."""
    _set_status(request_obj, Status.PACKAGE_ARRIVED, sync_plan=bool(request_obj.plan_id))
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
    _set_status(request_obj, Status.READY_FOR_PICKUP, sync_plan=bool(request_obj.plan_id))
    for party in (request_obj.buyer, _order_traveler(request_obj)):
        if not party:
            continue
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
    recipients = [u for u in req.chat_participants if u != message.sender]
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
    traveler = _order_traveler(request_obj)
    send_email(
        to_user=traveler,
        subject=f"Buyer requested reshipment — {request_obj.reference}",
        template="reship_requested",
        context=_ctx(request_obj),
        event="reship_requested",
    )
    notify_see_email(traveler, event="reship_requested")


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
    traveler = _order_traveler(request_obj)
    send_email(
        to_user=traveler,
        subject=f"Buyer uploaded reshipment payment proof — {request_obj.reference}",
        template="reship_proof_uploaded",
        context=_ctx(request_obj),
        event="reship_proof_uploaded",
    )
    notify_see_email(traveler, event="reship_proof_uploaded")


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
    # Flow-1: clearing only makes the proxy's held 2nd 50% and the carrier payout
    # ELIGIBLE — admin releases the actual funds (and sends the "released" notices)
    # from the order admin actions. Cargo/plan-first still auto-notify the payout
    # here, since those release at Clear in one step.
    # Order-level traveler payouts (Flow-1 proxy, plan-first) are released by
    # admin from the order/offer admin action, which sends the "paid" notice — so
    # no auto "being released" email here. (Buyer-first cargo pays per leg.)
    if not (request_obj.is_proxy_buyer_first or request_obj.plan_id):
        traveler = _order_traveler(request_obj)
        send_email(
            to_user=traveler,
            subject="Cleared — your full payment is being released",
            template="payout_released",
            context=_ctx(request_obj, payout=request_obj.transaction.payout_to_traveler),
            event="cleared",
        )
        notify_see_email(traveler, event="cleared")
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


# --- Flow-1 disbursement notifications (fired from the admin "mark paid" actions
#     once the money has actually been released, not at the status transition) ---
def notify_proxy_disbursed(order):
    """Single proxy disbursement (full net) released -> notify the proxy."""
    _notify_proxy(
        order,
        subject=f"Payment released — {order.reference}",
        template="proxy_final_disbursed",
        ctx=_ctx(order, amount=order.proxy_disbursement_total),
        event="proxy_disbursed",
    )


def notify_proxy_first_disbursed(order):
    _notify_proxy(
        order,
        subject=f"First payment released — {order.reference}",
        template="proxy_first_disbursed",
        ctx=_ctx(order, amount=order.proxy_disbursement_first_half),
        event="proxy_first_disbursed",
    )


def notify_proxy_second_disbursed(order):
    _notify_proxy(
        order,
        subject=f"Final payment released — {order.reference}",
        template="proxy_final_disbursed",
        ctx=_ctx(order, amount=order.proxy_disbursement_second_half),
        event="proxy_final_disbursed",
    )


def notify_traveler_paid(order):
    traveler = _order_traveler(order)
    if not traveler:
        return
    send_email(
        to_user=traveler,
        subject="Cleared — your payment is being released",
        template="payout_released",
        context=_ctx(order, payout=order.transaction.payout_to_traveler),
        event="cleared",
    )
    notify_see_email(traveler, event="cleared")


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
    # Flow-1: also notify the assigned proxy buyer (a traveler picked up their cargo).
    proxy_user = order.proxy_buyer.user if order.proxy_buyer_id else None
    if proxy_user and proxy_user.id != order.buyer_id:
        send_email(
            to_user=proxy_user,
            subject=f"A traveler offered to carry order {order.reference}",
            template="offer_received",
            context=ctx,
            event="offer_received",
        )
        notify_see_email(proxy_user, event="offer_received")
