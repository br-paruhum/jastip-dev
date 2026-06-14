from django.db import models


class Status(models.TextChoices):
    """The 8-step jastip lifecycle (+ rejection / cancellation)."""

    NEW = "new", "New"
    REQUEST_RECEIVED = "request_received", "W/f Estimate"
    ACCEPTED = "accepted", "Estimate Sent"
    REOPEN = "reopen", "Reopen"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"
    DEPOSIT_PAID = "deposit_paid", "Deposit Paid"
    ITEMS_PURCHASED = "items_purchased", "Package Ready"
    PACKAGE_ARRIVED = "package_arrived", "Package Arrived"
    READY_FOR_PICKUP = "ready_for_pickup", "Paid in Full"
    CLEAR = "clear", "Clear"
    CLOSED = "closed", "Closed"


# Statuses where the travel plan is still accepting a new buyer block.
OPEN_PLAN_STATUSES = {Status.NEW, Status.REOPEN}

# Chat is only available once there is a real purchase to discuss (deposit paid
# onward). Keeping it closed during the estimate stage reduces the temptation
# for buyers and travelers to exchange contact details and bypass the platform.
CHAT_STATUSES = {
    Status.DEPOSIT_PAID,
    Status.ITEMS_PURCHASED,
    Status.PACKAGE_ARRIVED,
    Status.READY_FOR_PICKUP,
    Status.CLEAR,
}

# Statuses considered an in-progress (current/open) transaction on the home page.
ACTIVE_TX_STATUSES = {
    Status.REQUEST_RECEIVED,
    Status.ACCEPTED,
    Status.DEPOSIT_PAID,
    Status.ITEMS_PURCHASED,
    Status.PACKAGE_ARRIVED,
    Status.READY_FOR_PICKUP,
    Status.CLEAR,
}

# Bootstrap-ish colour keyword per status, used for badges in templates/admin.
STATUS_TONE = {
    Status.NEW: "info",
    Status.REQUEST_RECEIVED: "warning",
    Status.ACCEPTED: "success",
    Status.REOPEN: "info",
    Status.REJECTED: "danger",
    Status.CANCELLED: "danger",
    Status.DEPOSIT_PAID: "success",
    Status.ITEMS_PURCHASED: "primary",
    Status.PACKAGE_ARRIVED: "primary",
    Status.READY_FOR_PICKUP: "warning",
    Status.CLEAR: "success",
    Status.CLOSED: "success",
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


_COUNTRY_NAMES = [
    "Australia", "Austria", "Bangladesh", "Belgium", "Brazil", "Brunei", "Cambodia",
    "Canada", "China", "Denmark", "Egypt", "Finland", "France", "Germany", "Hong Kong",
    "India", "Indonesia", "Ireland", "Italy", "Japan", "Jordan", "Kuwait", "Laos",
    "Macau", "Malaysia", "Maldives", "Mexico", "Myanmar", "Nepal", "Netherlands",
    "New Zealand", "Norway", "Oman", "Pakistan", "Philippines", "Poland", "Portugal",
    "Qatar", "Saudi Arabia", "Singapore", "South Korea", "Spain", "Sri Lanka", "Sweden",
    "Switzerland", "Taiwan", "Thailand", "Turkey", "United Arab Emirates",
    "United Kingdom", "United States", "Vietnam",
]

# (value, label) — stored as the country name to match the model CharFields.
COUNTRY_CHOICES = [("", "Select a country")] + [(n, n) for n in _COUNTRY_NAMES]


DEFAULT_PAYMENT_TERM = (
    "50% deposit of items ordered including margin + 100% of shipment cost. "
    "Custom duty at the destination city is paid by the buyer. "
    "This payment is reimbursable to the traveler with proof of payment."
)

# Pre-filled (editable) note the buyer sends to the traveler with a request.
DEFAULT_BUYER_NOTE = (
    "Please send me a message if you find difficulties finding my item(s) - "
    "(product, product size/type or quantity), for I can think for substitutions.\n"
    "Thank you."
)
