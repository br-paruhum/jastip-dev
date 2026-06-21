"""Transaction-type compatibility for the 4-flow taxonomy (see PLAN-flow-taxonomy.md).

A post's transaction type (proxy-buying vs carrying) is set by its initiator; a
responder must match it. These helpers centralise the match check and the
user-facing rejection copy, shared by the views (server-side enforcement) and the
templates (browse badges / adaptive buttons). Flows 2 & 3 will extend these checks.
"""

# Why a response is blocked — reused in views + templates so the wording is consistent.
CARRIER_PLAN_NEEDS_CARGO = (
    "This is a Carrier plan — the traveler only carries cargo you already have, they "
    "don't shop for items. Placing an item order here isn't available yet (the Cargo "
    "order flow is coming soon)."
)
PRODUCTS_ORDER_NEEDS_PROXY = (
    "This order needs a Proxy Buyer to purchase the items abroad. As a Carrier you can "
    "only respond to Cargo orders — carrying goods the buyer already has."
)


def plan_accepts_item_order(plan) -> bool:
    """An item/shopping order (request_create) is only valid on a Proxy Buyer plan."""
    return not plan.carrier_only


def order_accepts_carry_offer(order) -> bool:
    """A carry offer (offer_create) is valid on a Cargo buyer-first order, or on a
    Flow-1 Products order once the proxy buyer has sent the estimate (status
    responded) — the package is now sourced and "Looking for a Traveler"."""
    from .constants import Status
    if order.is_cargo:
        return True
    return bool(getattr(order, "proxy_buyer_id", None)) and order.status == Status.RESPONDED
