"""Status transitions + the email/WhatsApp notifications that accompany them.

Keeping the side effects here keeps views and admin actions thin and ensures
every lifecycle step sends the right email (cc admin) + WhatsApp reminder.
"""

from __future__ import annotations

from django.conf import settings

from apps.notifications.services import notify_see_email, send_email

from .constants import Status


def _site_url(path: str = "") -> str:
    domain = getattr(settings, "SITE_DOMAIN", "localhost:8019")
    scheme = "http" if settings.DEBUG else "https"
    return f"{scheme}://{domain}{path}"


def _ctx(request_obj, **extra):
    ctx = {
        "request_obj": request_obj,
        "plan": request_obj.plan,
        "buyer": request_obj.buyer,
        "traveler": request_obj.plan.traveler,
        "request_url": _site_url(request_obj.get_absolute_url()),
        "site_url": _site_url("/"),
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
    """Step 2: buyer submitted a request -> notify traveler."""
    _set_status(request_obj, Status.REQUEST_RECEIVED)
    send_email(
        to_user=request_obj.plan.traveler,
        subject="New buying request for your trip",
        template="request_received",
        context=_ctx(request_obj),
        event="request_received",
    )
    notify_see_email(request_obj.plan.traveler, event="request_received")


def on_request_accepted(request_obj):
    """Step 3 (accept): traveler priced + accepted -> notify buyer."""
    _set_status(request_obj, Status.ACCEPTED)
    send_email(
        to_user=request_obj.buyer,
        subject="Your request was accepted — please transfer the deposit",
        template="request_accepted",
        context=_ctx(request_obj),
        event="request_accepted",
    )
    notify_see_email(request_obj.buyer, event="request_accepted")


def on_request_rejected(request_obj, reason=""):
    """Step 3 (reject): traveler rejected -> reopen the plan, notify buyer."""
    request_obj.rejection_reason = reason
    request_obj.status = Status.REJECTED
    request_obj.save(update_fields=["status", "rejection_reason", "updated_at"])
    request_obj.plan.status = Status.REOPEN
    request_obj.plan.save(update_fields=["status", "updated_at"])
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


def on_items_purchased(request_obj):
    """Step 5: traveler recorded purchases -> invoice ready, notify buyer."""
    _set_status(request_obj, Status.ITEMS_PURCHASED)
    send_email(
        to_user=request_obj.buyer,
        subject="Your items have been purchased",
        template="items_purchased",
        context=_ctx(request_obj),
        event="items_purchased",
    )
    notify_see_email(request_obj.buyer, event="items_purchased")


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


def on_cleared(request_obj):
    """Step 8: both parties confirmed clearance -> close.

    This is when the traveler is paid: admin transfers the full invoice amount
    less the 2.5% platform fee. The buyer gets a thank-you note.
    """
    _set_status(request_obj, Status.CLOSED)
    send_email(
        to_user=request_obj.plan.traveler,
        subject="Transaction closed — your payment has been transferred",
        template="payout_released",
        context=_ctx(request_obj, payout=request_obj.transaction.payout_to_traveler),
        event="closed",
    )
    notify_see_email(request_obj.plan.traveler, event="closed")
    send_email(
        to_user=request_obj.buyer,
        subject="Transaction closed — thank you",
        template="closed",
        context=_ctx(request_obj),
        event="closed",
    )
