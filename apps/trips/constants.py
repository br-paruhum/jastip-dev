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
    OPEN = "open", "Awaiting Carrier"
    ESTIMATE_SENT = "estimate_sent", "Estimate Sent"
    RESPONDED = "responded", "Responded"
    TAKEN = "taken", "Taken"
    PACKAGE_DROPPED_OFF = "package_dropped_off", "Package Dropped Off"
    WEIGHT_VERIFIED = "weight_verified", "Weight Verified"
    PACKAGE_RECEIVED = "package_received", "Package Received"
    NO_RESPONSE = "no_response", "No Response"
    DROPOFF_MISSED = "dropoff_missed", "Dropoff Missed"


# Order-level statuses that only exist on buyer-first orders (Order.plan is null).
BUYER_FIRST_STATUSES = {
    Status.OPEN, Status.ESTIMATE_SENT, Status.RESPONDED, Status.TAKEN,
    Status.PACKAGE_DROPPED_OFF, Status.WEIGHT_VERIFIED, Status.PACKAGE_RECEIVED,
    Status.NO_RESPONSE, Status.DROPOFF_MISSED,
}

# Buyer-first orders still accepting TravelerOffers.
OPEN_ORDER_STATUSES = {Status.OPEN, Status.RESPONDED}

# Terminal buyer-first statuses (order never matched, or matching failed).
BUYER_FIRST_TERMINAL_STATUSES = {Status.NO_RESPONSE, Status.DROPOFF_MISSED}

# Proxy/products order statuses where the buyer's deposit is verified and the
# carrier's capacity is committed against their published trip — so the spare
# weight advertised on the Queuing Traveler's Offers board drops by this order's
# weight (mirrors cargo's SELECTED + deposit_verified). Excludes pre-deposit
# states (still advertised), and CLEAR/CLOSED/CANCELLED (capacity released).
DEPOSIT_COMMITTED_STATUSES = {
    Status.DEPOSIT_PAID, Status.ITEMS_PURCHASED, Status.PACKAGE_RECEIVED,
    Status.PACKAGE_ARRIVED, Status.READY_FOR_PICKUP, Status.RESHIP_REQUESTED,
    Status.RESHIP_COST_SENT, Status.RESHIPPING,
}


class OfferStatus(models.TextChoices):
    """Lifecycle of a single TravelerOffer before it is matched."""

    PENDING = "pending", "Pending"
    SELECTED = "selected", "Selected"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"


class LegStatus(models.TextChoices):
    """Per-leg lifecycle once a TravelerOffer is selected (offer_status=SELECTED).
    Mirrors a subset of Status — kept separate since several legs can be in
    flight at once for the same Order (partial fulfillment)."""

    PACKAGE_DROPPED_OFF = "package_dropped_off", "Package Dropped Off"
    WEIGHT_VERIFIED = "weight_verified", "Weight Verified"
    PACKAGE_RECEIVED = "package_received", "Package Received"
    PACKAGE_ARRIVED = "package_arrived", "Package Arrived"
    READY_FOR_PICKUP = "ready_for_pickup", "Ready for Pickup"
    # Same DB values as Status's reship trio, so Order.recompute_status()'s
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


class MatchStatus(models.TextChoices):
    """Lifecycle of a CarrierMatch — the carrier-first surfacing/hold record that
    links a RESPONDED order to a queued TravelPlan (see PLAN-carrier-first-orders.md).

    ``pending`` and ``accepted`` rows both hold weight against the plan's queue
    capacity; ``expired``/``rejected`` release it."""

    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    EXPIRED = "expired", "Expired"
    REJECTED = "rejected", "Rejected"


class MatchSource(models.TextChoices):
    """How a CarrierMatch was created — for analytics only."""

    PUSH = "push", "Auto-surfaced"   # matcher put it on the buyer's page
    PULL = "pull", "Buyer picked"    # buyer clicked "Send Order" on the board


# CarrierMatch rows that still reserve weight against a plan's queue capacity.
HELD_MATCH_STATUSES = {MatchStatus.PENDING, MatchStatus.ACCEPTED}


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

# --- Task 11: Message-tab visibility per references/how-to/How-to_260625.pdf ---
# The foldable "Message" tab is locked for everyone (admin excepted) until the
# buyer's deposit is verified, then rotates through a *pair* of parties as the
# proxy transaction advances. See Order.message_tab_visible_to().
#   note 5  -> Buyer  + Proxy   (deposit paid; proxy starts sourcing)
#   note 6  -> Proxy  + Carrier (proxy sent actual cost / "Package Ready")
#   note 8+ -> Carrier + Buyer  (carrier received the package, through close)
# Buyer<->Proxy opens as soon as the proxy is assigned (Step-2.1, status OPEN) so
# the two can discuss product issues (out-of-stock, shortages, etc.) while the
# proxy prepares the estimate, and stays open through the pre-deposit stages, not
# just from DEPOSIT_PAID onward.
MSG_TAB_BUYER_PROXY = {
    Status.OPEN, Status.ESTIMATE_SENT, Status.RESPONDED, Status.ACCEPTED,
    Status.DEPOSIT_PAID,
}
MSG_TAB_PROXY_CARRIER = {Status.ITEMS_PURCHASED}
MSG_TAB_CARRIER_BUYER = {
    Status.PACKAGE_RECEIVED, Status.PACKAGE_ARRIVED, Status.READY_FOR_PICKUP,
    Status.RESHIP_REQUESTED, Status.RESHIP_COST_SENT, Status.RESHIPPING,
    Status.CLEAR, Status.CLOSED,
}
# Non-proxy (2-party Buyer+Carrier) orders have no proxy slot to rotate through,
# so the tab simply opens to both once the order is an active transaction
# (deposit paid / package handed over) and stays open through close.
MSG_TAB_TWO_PARTY = (
    (MSG_TAB_BUYER_PROXY | MSG_TAB_PROXY_CARRIER | MSG_TAB_CARRIER_BUYER)
    - {Status.OPEN}  # a 2-party order at OPEN has no carrier yet — no chat
    | {Status.TAKEN, Status.PACKAGE_DROPPED_OFF, Status.WEIGHT_VERIFIED}
)

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

# Orders that may have a buyer overpayment to refund: any status from arrival
# onward (invoice is final), excluding cancelled/rejected. Because the buyer pays
# 100% upfront, an overpaid order auto-settles past PACKAGE_ARRIVED straight to
# READY_FOR_PICKUP, so the refund is owed across this whole range — not just at
# PACKAGE_ARRIVED. Drives the Pending Disbursement refund rows + release guard.
REFUND_ELIGIBLE_STATUSES = {
    Status.PACKAGE_ARRIVED,
    Status.READY_FOR_PICKUP,
    Status.RESHIP_REQUESTED,
    Status.RESHIP_COST_SENT,
    Status.RESHIPPING,
    Status.CLEAR,
    Status.CLOSED,
}

# Bootstrap-ish colour keyword per status, used for badges in templates/admin.
# Per user (22-Jun-2026): every order/transaction status badge renders blue
# ("info") — no per-status colour. All statuses map to "info"; the status_tone
# fallbacks below also default to "info" so any unmapped status stays blue too.
STATUS_TONE = {
    status: "info"
    for status in [
        Status.NEW, Status.REQUEST_RECEIVED, Status.ACCEPTED, Status.REOPEN,
        Status.REJECTED, Status.CANCELLED, Status.DEPOSIT_PAID, Status.ITEMS_PURCHASED,
        Status.PACKAGE_ARRIVED, Status.READY_FOR_PICKUP, Status.RESHIP_REQUESTED,
        Status.RESHIP_COST_SENT, Status.RESHIPPING, Status.CLEAR, Status.CLOSED,
        Status.OPEN, Status.ESTIMATE_SENT, Status.RESPONDED, Status.TAKEN,
        Status.PACKAGE_DROPPED_OFF, Status.WEIGHT_VERIFIED, Status.PACKAGE_RECEIVED,
        Status.NO_RESPONSE, Status.DROPOFF_MISSED,
    ]
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

# ISO 3166-1 alpha-2 codes for the customs invoice "Country of Origin" column,
# which shows the short code (e.g. "ID") rather than the full country name.
COUNTRY_ISO2 = {
    "Australia": "AU", "Austria": "AT", "Bangladesh": "BD", "Belgium": "BE",
    "Brazil": "BR", "Brunei": "BN", "Cambodia": "KH", "Canada": "CA", "China": "CN",
    "Denmark": "DK", "Egypt": "EG", "Finland": "FI", "France": "FR", "Germany": "DE",
    "Hong Kong": "HK", "India": "IN", "Indonesia": "ID", "Ireland": "IE", "Italy": "IT",
    "Japan": "JP", "Jordan": "JO", "Kuwait": "KW", "Laos": "LA", "Macau": "MO",
    "Malaysia": "MY", "Maldives": "MV", "Mexico": "MX", "Myanmar": "MM", "Nepal": "NP",
    "Netherlands": "NL", "New Zealand": "NZ", "Norway": "NO", "Oman": "OM",
    "Pakistan": "PK", "Philippines": "PH", "Poland": "PL", "Portugal": "PT",
    "Qatar": "QA", "Saudi Arabia": "SA", "Singapore": "SG", "South Korea": "KR",
    "Spain": "ES", "Sri Lanka": "LK", "Sweden": "SE", "Switzerland": "CH",
    "Taiwan": "TW", "Thailand": "TH", "Turkey": "TR", "United Arab Emirates": "AE",
    "United Kingdom": "GB", "United States": "US", "Vietnam": "VN",
}


DEFAULT_PAYMENT_TERM = (
    "100% of items ordered including margin + 100% of shipment cost, paid upfront. "
    "Custom duty at the destination city is paid by the buyer. "
    "This payment is reimbursable to the carrier with proof of payment."
)

# Separate payment terms shown on Carrier Only travel plans (no proxy buying).
DEFAULT_PAYMENT_TERM_CARRIER = (
    "<ul>"
    "<li>Full payment of shipment cost should be paid at the time Buyer hand "
    "over the package to Carrier.</li>"
    "<li>Custom duty at the destination city is paid Buyer. Carrier will cover "
    "the duty payment and should be reimbursed upon showing the proof of payment.</li>"
    "</ul>"
)

# Payment terms shown to the Buyer on a Cargo order (buyer already owns the goods,
# so no Proxy Buyer and no goods cost — only the carry fee + platform fee).
# This default is what the pages/0015 migration writes onto the existing settings
# row on staging/production, so keep it in sync with the wording in admin.
DEFAULT_PAYMENT_TERM_CARGO_BUYER = """<ul>
<li>Buyer pay both 1). Total of Shipment Cost when Buyer accept shipment rate offered by a Carrier and 2). 2.5% platform fee to goProxyBuy.com 
</li>
<li>Buyer pay both 1). Customs Duty; and 2). Shipment Cost shortage (Actual minus Estimate) if any; when Traveler arrived at the destination city and upload Customs Duty amount.
</li>
<li>Delivery End-Point of the package is based on Buyer selection when placing the order. If Buyer selected "Request Reship" then the Delivery End-Point is the Buyer's location, else is the Traveler's location. 
</li>
<li>Proxy Buyer and Traveler payouts will be disbursed maximum 24 hours after Buyer confirm "Package Received"</li>
</ul>"""

# Pre-filled (editable) note the buyer sends to the traveler with a request.
DEFAULT_BUYER_NOTE = (
    "Please send me a message if you find difficulties finding my item(s) - "
    "(product, product size/type or quantity), for I can think for substitutions.\n"
    "Thank you."
)
