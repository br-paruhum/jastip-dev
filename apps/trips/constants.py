from django.db import models


class Status(models.TextChoices):
    """The 8-step jastip lifecycle (+ rejection)."""

    NEW = "new", "New"
    REQUEST_RECEIVED = "request_received", "Request Received"
    ACCEPTED = "accepted", "Accepted"
    REOPEN = "reopen", "Reopen"
    REJECTED = "rejected", "Rejected"
    DEPOSIT_PAID = "deposit_paid", "Deposit Paid"
    ITEMS_PURCHASED = "items_purchased", "Item(s) Purchased"
    PACKAGE_ARRIVED = "package_arrived", "Package Arrived"
    READY_FOR_PICKUP = "ready_for_pickup", "Ready for Pickup"
    CLOSED = "closed", "Closed"


# Statuses where the travel plan is still accepting a new buyer block.
OPEN_PLAN_STATUSES = {Status.NEW, Status.REOPEN}

# Statuses considered an in-progress (current/open) transaction on the home page.
ACTIVE_TX_STATUSES = {
    Status.REQUEST_RECEIVED,
    Status.ACCEPTED,
    Status.DEPOSIT_PAID,
    Status.ITEMS_PURCHASED,
    Status.PACKAGE_ARRIVED,
    Status.READY_FOR_PICKUP,
}

# Bootstrap-ish colour keyword per status, used for badges in templates/admin.
STATUS_TONE = {
    Status.NEW: "info",
    Status.REQUEST_RECEIVED: "warning",
    Status.ACCEPTED: "success",
    Status.REOPEN: "info",
    Status.REJECTED: "danger",
    Status.DEPOSIT_PAID: "success",
    Status.ITEMS_PURCHASED: "primary",
    Status.PACKAGE_ARRIVED: "primary",
    Status.READY_FOR_PICKUP: "warning",
    Status.CLOSED: "muted",
}


class Currency(models.TextChoices):
    IDR = "IDR", "IDR — Indonesian Rupiah"
    USD = "USD", "USD — US Dollar"
    SGD = "SGD", "SGD — Singapore Dollar"
    MYR = "MYR", "MYR — Malaysian Ringgit"
    EUR = "EUR", "EUR — Euro"
    GBP = "GBP", "GBP — British Pound"
    AUD = "AUD", "AUD — Australian Dollar"
    JPY = "JPY", "JPY — Japanese Yen"
    CNY = "CNY", "CNY — Chinese Yuan"
    HKD = "HKD", "HKD — Hong Kong Dollar"
    KRW = "KRW", "KRW — South Korean Won"
    THB = "THB", "THB — Thai Baht"


DEFAULT_PAYMENT_TERM = (
    "50% deposit of items ordered + 100% of shipment cost. "
    "Custom fare at the destination country is paid by the buyer "
    "(reimbursable to the traveler with proof of payment)."
)
