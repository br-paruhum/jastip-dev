from django.db import models


class Status(models.TextChoices):
    """The 8-step jastip lifecycle (+ rejection / cancellation), plus the
    buyer-first order-level statuses (OPEN..DROPOFF_MISSED below)."""

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
    RESHIP_REQUESTED = "reship_requested", "Reship Requested"
    RESHIP_COST_SENT = "reship_cost_sent", "Reship Cost Sent"
    RESHIPPING = "reshipping", "In Transit"
    CLEAR = "clear", "Clear"
    CLOSED = "closed", "Closed"

    # --- Buyer-first order-level statuses ---
    OPEN = "open", "Awaiting Traveler"
    RESPONDED = "responded", "Responded"
    TAKEN = "taken", "Taken"
    PACKAGE_DROPPED_OFF = "package_dropped_off", "Package Dropped Off"
    WEIGHT_VERIFIED = "weight_verified", "Weight Verified"
    PACKAGE_RECEIVED = "package_received", "Package Received"
    NO_RESPONSE = "no_response", "No Response"
    DROPOFF_MISSED = "dropoff_missed", "Dropoff Missed"


# Order-level statuses that only exist on buyer-first orders (BuyRequest.plan is null).
BUYER_FIRST_STATUSES = {
    Status.OPEN, Status.RESPONDED, Status.TAKEN,
    Status.PACKAGE_DROPPED_OFF, Status.WEIGHT_VERIFIED, Status.PACKAGE_RECEIVED,
    Status.NO_RESPONSE, Status.DROPOFF_MISSED,
}

# Buyer-first orders still accepting TravelerOffers.
OPEN_ORDER_STATUSES = {Status.OPEN, Status.RESPONDED}

# Terminal buyer-first statuses (order never matched, or matching failed).
BUYER_FIRST_TERMINAL_STATUSES = {Status.NO_RESPONSE, Status.DROPOFF_MISSED}


class OfferStatus(models.TextChoices):
    """Lifecycle of a single TravelerOffer before it is matched."""

    PENDING = "pending", "Pending"
    SELECTED = "selected", "Selected"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"


class LegStatus(models.TextChoices):
    """Per-leg lifecycle once a TravelerOffer is selected (offer_status=SELECTED).
    Mirrors a subset of Status — kept separate since several legs can be in
    flight at once for the same BuyRequest (partial fulfillment)."""

    PACKAGE_DROPPED_OFF = "package_dropped_off", "Package Dropped Off"
    WEIGHT_VERIFIED = "weight_verified", "Weight Verified"
    PACKAGE_RECEIVED = "package_received", "Package Received"
    PACKAGE_ARRIVED = "package_arrived", "Package Arrived"
    READY_FOR_PICKUP = "ready_for_pickup", "Ready for Pickup"
    # Same DB values as Status's reship trio, so BuyRequest.recompute_status()'s
    # `Status(leg.leg_status)` cast keeps working for the order-level rollup.
    RESHIP_REQUESTED = "reship_requested", "Reship Requested"
    RESHIP_COST_SENT = "reship_cost_sent", "Reship Cost Sent"
    RESHIPPING = "reshipping", "In Transit"
    CLEAR = "clear", "Clear"
    CLOSED = "closed", "Closed"
    DROPOFF_MISSED = "dropoff_missed", "Dropoff Missed"


class FulfillmentMethod(models.TextChoices):
    PICKUP = "pickup", "Pickup"
    RESHIP = "reship", "Reship"


# Statuses where the travel plan is still accepting a new buyer block.
OPEN_PLAN_STATUSES = {Status.NEW, Status.REOPEN}

# Chat is only available once there is a real purchase to discuss (deposit paid
# onward). Keeping it closed during the estimate stage reduces the temptation
# for buyers and travelers to exchange contact details and bypass the platform.
CHAT_STATUSES = {
    Status.ACCEPTED,
    Status.DEPOSIT_PAID,
    Status.ITEMS_PURCHASED,
    Status.PACKAGE_RECEIVED,
    Status.PACKAGE_ARRIVED,
}

# Statuses considered an in-progress (current/open) transaction on the home page.
ACTIVE_TX_STATUSES = {
    Status.REQUEST_RECEIVED,
    Status.ACCEPTED,
    Status.DEPOSIT_PAID,
    Status.ITEMS_PURCHASED,
    Status.PACKAGE_ARRIVED,
    Status.READY_FOR_PICKUP,
    Status.RESHIP_REQUESTED,
    Status.RESHIP_COST_SENT,
    Status.RESHIPPING,
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
    Status.RESHIP_REQUESTED: "warning",
    Status.RESHIP_COST_SENT: "warning",
    Status.RESHIPPING: "info",
    Status.CLEAR: "success",
    Status.CLOSED: "success",
    Status.OPEN: "info",
    Status.RESPONDED: "warning",
    Status.TAKEN: "success",
    Status.PACKAGE_DROPPED_OFF: "primary",
    Status.WEIGHT_VERIFIED: "primary",
    Status.PACKAGE_RECEIVED: "primary",
    Status.NO_RESPONSE: "danger",
    Status.DROPOFF_MISSED: "danger",
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

# Separate payment terms shown on Carrier Only travel plans (no proxy buying).
DEFAULT_PAYMENT_TERM_CARRIER = (
    "<ul>"
    "<li>Full payment of shipment cost should be paid at the time Buyer hand "
    "over the package to Traveler.</li>"
    "<li>Custom duty at the destination city is paid Buyer. Traveler will cover "
    "the duty payment and should be reimbursed upon showing the proof of payment.</li>"
    "</ul>"
)

# Pre-filled (editable) note the buyer sends to the traveler with a request.
DEFAULT_BUYER_NOTE = (
    "Please send me a message if you find difficulties finding my item(s) - "
    "(product, product size/type or quantity), for I can think for substitutions.\n"
    "Thank you."
)
