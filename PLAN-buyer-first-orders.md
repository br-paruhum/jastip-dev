# Plan — Buyer-First Orders (Bid/Ask + Partial Fulfillment Auction)

Status: **DESIGN FINALISED (v2)** · reviewed & annotated by user 16-Jun-2026 · ready to implement

---

## 1. Goal

Add a second, opposite flow to the platform.
Today = **plan-first**: traveler posts a `TravelPlan`, buyers order against it.
New = **order-first**: buyer posts an order with no traveler yet; travelers respond,
and — new in v2 — **a single order can be split across multiple travelers** if the
buyer allows it and no single traveler has enough capacity.
Both flows run side by side — the existing plan-first flow is untouched.

---

## 2. Pricing — Bid / Ask (auction, with partial fulfillment)

- **Bid** — buyer's offered shipment cost per kg, set when the order is created.
- **Ask** — each responding traveler's own rate, set in their `TravelerOffer`.
- Each traveler also declares **Avail (kg)** — their carrying capacity. Shown **publicly**
  (unlike the drop-off address).
- Buyer reviews all asks privately and picks one or more offers.
- The public Open Orders page shows a derived label ("Responded" / "Taken") — individual
  ask amounts stay hidden from the public and other travelers.

### Partial fulfillment
- Buyer sets **Partial Allowed** (Yes/No) when posting the order.
- If **Yes** and a single traveler's `Avail kg` < buyer's `bid_weight_kg`, the order stays
  open for more offers ("Responded") even after one offer is accepted.
- Buyer must **physically split the package**: each portion handed to a traveler must be
  **≤ that traveler's Avail kg**, never more.
- The order becomes "Taken" (no longer accepting offers) once the sum of all confirmed
  legs' allocated weight meets the buyer's `bid_weight_kg`.

### Payment (per leg)
- Each confirmed `TravelerOffer` ("leg") has its own deposit:
  **deposit = that leg's allocated weight × that leg's accepted ask rate.**
- Traveler's name and drop-off address are **not disclosed until that leg's deposit clears**.
- Traveler re-weighs at handover; weight delta is settled at that leg's `PACKAGE_ARRIVED`.

---

## 3. New model — `TravelerOffer`

An offer becomes a **leg** once selected by the buyer — it then carries its own mini
lifecycle independent of other legs on the same order.

| Field | Type | Notes |
|---|---|---|
| `order` | FK → BuyRequest | The order being responded to |
| `traveler` | FK → User | Responding traveler |
| `ask_cost_per_kg` | Decimal | Traveler's quoted rate |
| `avail_kg` | Decimal | Traveler's declared capacity — **shown publicly** |
| `drop_off_address` | TextField | Hidden from buyer until this leg's deposit clears |
| `drop_off_postal_code` | Char | Same visibility as drop_off_address (buyer-dashboard only) |
| `pickup_address` | TextField | Destination pickup point. Hidden until `PACKAGE_ARRIVED` **and** buyer chooses Pickup (not Reship) |
| `travel_date` | Date | Drop-off deadline = travel_date − 1 day |
| `travel_time` | Time | For precise cron scheduling |
| `from_city / from_country` | Char | Traveler's departure |
| `to_city / to_country` | Char | Traveler's destination |
| `offer_status` | choices | `pending / selected / rejected / withdrawn` |
| `allocated_weight_kg` | Decimal | Set at selection; must be ≤ `avail_kg` |
| `leg_status` | choices | See lifecycle below — only meaningful once `offer_status = selected` |
| `agreed_weight_kg` | Decimal | Traveler's final measured weight (set at `weight_verified`) |
| `fulfillment_method` | choices | `pickup / reship` — set at `package_arrived` |
| `dropped_off_at` | DateTime | |
| `weight_verified_at` | DateTime | |
| `received_at` | DateTime | |
| `arrived_at` | DateTime | |
| `created_at` | DateTime | Auto |

A traveler may withdraw their own offer (→ `withdrawn`) at any time before the buyer
selects it.

### `leg_status` lifecycle (per selected offer)
```
package_dropped_off → weight_verified → package_received → package_arrived
  → ready_for_pickup → clear → closed
```
Terminal failure: `dropoff_missed` (grace period expired with no drop-off; see §6).

---

## 4. `BuyRequest` changes

- `plan` → made **nullable** (buyer-first orders have no plan at creation).
- New fields (all nullable; only used when `plan` is null):

| Field | Type | Notes |
|---|---|---|
| `from_city / from_country` | Char | Buyer's stated route |
| `to_city / to_country` | Char | Buyer's stated destination |
| `to_address / to_postal_code` | Char/Text | Lets traveler see upfront if reship is needed at destination |
| `settlement_currency` | Char | Buyer picks; today comes from plan |
| `max_acceptable_date` | Date | Offer deadline **and** drop-off grace-period deadline (see §6) |
| `bid_weight_kg` | Decimal | Renamed from "stated weight" |
| `bid_cost_per_kg` | Decimal | Buyer's opening price per kg |
| `partial_allowed` | Boolean | Buyer opts in to multi-traveler split fulfillment |

### Order-level status — computed rollup
`BuyRequest.status` is **not** independently set once legs exist — it's a rollup:

- No confirmed legs yet → `open` or `responded` (derived from whether ≥1 pending offer exists)
- Sum of confirmed legs' `allocated_weight_kg` ≥ `bid_weight_kg` → `taken`
- ≥1 leg confirmed and in progress → **status mirrors the least-progressed active leg**
  (e.g. one leg at `package_arrived`, another still at `package_dropped_off` →
  order shows `package_dropped_off`)
- All legs reach `closed` → order status `closed`

Resolver properties (`route`, `currency`, `traveler_user`, `effective_cost_per_kg`) fall
back to plan when present, else use the order's own fields.

---

## 5. Statuses & lifecycle

### New order-level statuses

| DB value | Display label | Trigger |
|---|---|---|
| `open` | Awaiting Traveler | Buyer submits buyer-first order |
| `responded` | Responded | ≥1 TravelerOffer exists, order not fully matched |
| `taken` | Taken | Confirmed legs' allocated weight covers `bid_weight_kg`; other travelers see "Place Offer" muted |
| `package_dropped_off` | Package Dropped Off | Rollup: least-progressed active leg is here |
| `weight_verified` | Weight Verified | Rollup |
| `package_received` | Package Received | Rollup |
| `no_response` | No Response | Cron: `max_acceptable_date` passed, zero offers ever received |
| `dropoff_missed` | Dropoff Missed | Cron: grace period (until `max_acceptable_date`) expired with no drop-off on the only/last leg |

`package_arrived`, `ready_for_pickup`, `clear`, `closed` reuse the **existing** Status
values as-is (no change to DB value or label).

### Complete buyer-first flow

```
OPEN
  └─ [traveler(s) submit TravelerOffers, each with ask + avail_kg + drop-off + travel date/time]
  └─ RESPONDED  (≥1 offer exists; public sees "Responded")
  └─ [buyer selects one or more offers — partial split if Partial Allowed]
       └─ per leg: deposit (allocated_kg × accepted ask) paid → address revealed
  └─ TAKEN  (sum of confirmed legs' allocated weight ≥ bid_weight_kg; no more offers accepted)

  ── per leg, independent progress ──────────────────────────────
  PACKAGE_DROPPED_OFF  ← buyer brings that leg's portion by travel_date − 1 day
    └─ WEIGHT_VERIFIED  ← traveler re-weighs; final, no dispute
       └─ PACKAGE_RECEIVED  ← traveler confirms custody
          └─ PACKAGE_ARRIVED (existing)  ← weight delta settled; buyer chooses Pickup or Reship
             └─ READY_FOR_PICKUP (existing) → CLEAR (existing) → CLOSED (existing)

  ── leg failure ─────────────────────────────────────────────────
  PACKAGE_DROPPED_OFF deadline missed
    └─ grace period until order's max_acceptable_date
       └─ [buyer finds replacement traveler before deadline → admin manually
            reassigns the paid deposit to the new offer]
       └─ [grace period also expires] → DROPOFF_MISSED (refund minus 2.5% platform fee)

── order-level terminal ───────────────────────────────────────────
NO_RESPONSE   ← cron: max_acceptable_date passed, zero offers ever
```

### Key business rules
- Traveler's measured weight is **always final** — no dispute flow.
- Drop-off deadline = `leg.travel_date − 1 day`.
- Traveler's name and address hidden until that leg's deposit clears.
- A traveler may withdraw their offer before being selected.
- Buyer may split the order across multiple travelers (if `partial_allowed`), each
  portion ≤ that traveler's `avail_kg`.
- At `PACKAGE_ARRIVED`, buyer chooses **Pickup** (traveler's destination address revealed
  only now) or **Reship** (reuses existing `RESHIP_REQUESTED → RESHIP_COST_SENT →
  RESHIPPING` statuses).
- Missed drop-off → grace period until `max_acceptable_date` → admin can manually move
  the deposit to a replacement traveler, or system auto-cancels with a 2.5% fee deducted
  from the refund.

---

## 6. Auto-expire / auto-fail crons

Two nightly management commands (mirroring the existing `close_cleared` pattern):

1. **`expire_open_orders`** — flips any order past `max_acceptable_date` with **zero**
   offers ever received → `NO_RESPONSE`.
2. **`expire_missed_dropoffs`** — for any leg past its drop-off deadline
   (`travel_date − 1 day`) that's still not `package_dropped_off`: if the order's
   `max_acceptable_date` has also passed → cancel that leg, refund minus 2.5% fee,
   set `dropoff_missed`. If `max_acceptable_date` hasn't passed yet, leave it — buyer
   is still in the grace period and may get a manual deposit reassignment.

Add both to staging & production crontabs (Asia/Jakarta, clear of existing 01:00/01:30
close crons and 03:00 backup).

---

## 7. Home page — 3 sections (buyer-first only)

Existing plan-first flow has 2 sections: "Open Travel Plans" and "Closed Transactions."
Buyer-first needs **3**, classified by time vs. (per-leg or order) travel date, not by
status alone:

| Time | Status | Displayed on |
|---|---|---|
| Before travel date+time | Any | **Open Orders** |
| After travel date+time | Any, except `closed` | **Orders in Transit** |
| After travel date+time | `closed` | **Closed Orders** |

This classification kicks in once a leg reaches `PACKAGE_RECEIVED` and is purely a
display/query layer — no extra status needed.

---

## 7a. Navigation & dashboard separation

- New top-nav item **"Order First"**, placed immediately before "How To" — a
  dedicated page for the buyer-first flow (separate from Home/traveler-first).
- The personal dashboard (`accounts:profile`) also gets separate buyer-first
  sections/tabs, not merged into the existing plan-first tables — same
  reasoning as §7 (different statuses, different per-row actions).

---

## 8. Screens

- **Buyer:** "Place an order" form — route, items, **bid weight**, bid rate, currency,
  deadline, **Partial Allowed** toggle, destination address/postal code.
- **Public:** "Open Orders" list — bid, route, deadline, **Avail (kg)** per responding
  traveler, "Responded"/"Taken" badge (Place Offer button muted when Taken).
- **Traveler:** Respond form — ask rate, **Avail (kg)**, drop-off address + postal code,
  pickup address, travel date **+ time**, route. Withdraw button.
- **Buyer:** Offers inbox (private) — list of TravelerOffers; select one or more
  (partial), pay per-leg deposit, get that leg's address.
- **Buyer:** Package-dropped-off action, per leg.
- **Traveler:** Weight-verified form (enter final weight) + Package-received
  confirmation, per leg.
- **Buyer:** Pickup vs. Reship choice at `PACKAGE_ARRIVED`.
- **Admin:** manual deposit-reassignment action (move a leg's payment to a replacement
  traveler during the grace period).
- Bid/Ask/Avail columns on home tables and order detail, consistent with existing columns.

---

## 9. Migrations

All additive: new nullable fields on `BuyRequest`, `plan` nullable, new `TravelerOffer`
table with leg-lifecycle fields, new status values. No destructive changes. Back up
prod DB before applying.

---

## 10. Build order (each verified on staging first)

1. New statuses in `constants.py` + `BuyRequest` field additions + `TravelerOffer` model
   (incl. leg fields) + migrations.
2. Resolver properties + order-level status rollup logic on `BuyRequest`.
3. Buyer "place order" flow + `OPEN` status + Partial Allowed toggle.
4. Public "Open Orders" list + Traveler offer/withdraw flow + Avail(kg) display.
5. Buyer offer inbox + select (single or partial-multi) + per-leg deposit payment +
   address reveal.
6. Per-leg actions: `PACKAGE_DROPPED_OFF` → `WEIGHT_VERIFIED` → `PACKAGE_RECEIVED`.
7. Pickup/Reship choice + weight-delta settlement at `PACKAGE_ARRIVED`.
8. `expire_open_orders` + `expire_missed_dropoffs` crons + crontab entries.
9. Admin manual deposit-reassignment action.
10. Bid/Ask/Avail columns on home tables + 3-section home page classification.
11. End-to-end staging test (including a partial-fulfillment + a missed-dropoff
    scenario) → deploy production.

---

## 11. Rollout

Branch `new-buyer-first-orders` off `main`.
Local → staging → production, same loop as prior features.
