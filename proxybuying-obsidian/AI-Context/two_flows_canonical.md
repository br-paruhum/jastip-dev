# The Two Flows — Canonical Reference

> Recorded 2026-07-07 from the user (br-paruhum) during Carrier-First testing, to
> resolve a lingering misunderstanding. **There are ONLY 2 flows. There is no other way.**
> This supersedes every earlier "3-flow" / "4-flow" description anywhere in the repo or
> AI-Context. Next discussion scheduled: **Friday, 2026-07-10, 03:00 WIB.**

Both flows converge on the **same downstream** (Proxy Buyer estimate → Buyer accepts →
deposit → purchase → carry → arrive → clear). They differ ONLY in **who arrives first**
and therefore **whether a carrier is already attached** before the estimate.

---

## Flow 1 — Buyer-First (existing, already in place)
*Buyer comes first and there is no Carrier matching his plan yet.*

- **a)** Buyer clicks **Order** on the Proxy Buyer List, fills in the wanted products,
  quantity, and deadline, clicks **Send**. → Order goes to the **Proxy Buyer**.
- **b)** Proxy Buyer fills in the **estimate cost, estimate total weight, and margin**,
  clicks **Send**. → Order goes to the **Buyer** for acceptance.
- **c)** If the Buyer accepts, the Order is listed under **"Queuing Cargo"**.
- **d)** A Carrier comes, sees the matching Queuing Cargo, clicks **Offer**, and fills in
  **travel date** and **Carry Rate per kg**.
- **e)** The Offer goes to the **Buyer** for acceptance. If the Buyer accepts, the Buyer is
  asked to make the **deposit**.

## Flow 2 — Carrier-First (new, currently under test)
*Carrier comes first and there is no Buyer matching his plan yet.*

- **a)** Carrier logs in, clicks **Create New Plan**, fills in **Travel Date, Available
  Weight, Carry Rate per kg**, clicks **Send**. → Data is listed on the **"Queuing
  Carrier"** board.
- **b)** A Buyer comes, sees the matching Carrier, clicks **Order**, fills in the wanted
  products, quantity, and deadline, clicks **Send** *(same as 1-a)*. → Order goes to the
  **Proxy Buyer**.
- **c)** Proxy Buyer fills in the **estimate cost, weight, and margin**, clicks **Send**. →
  Order goes to the **Buyer** for acceptance *(same as 1-b)*.
- **d)** If the Buyer accepts the estimate, the Buyer is asked to make the **deposit**
  *(same as 1-e)*.
  - **Key difference:** there is **no step equivalent to 1-c or 1-d** in this flow,
    because the **Carrier's Offer is already attached from step 2-b onward**. Acceptance
    of the estimate goes straight to deposit.

### Weight-hold & the 3-hour window (CLARIFIED 2026-07-07)
The "3 hours" is **NOT** a window for the Buyer to accept/reject the Carrier — the Buyer
already picked the Carrier at 2-b, so there is **no Carrier Accept/Reject step** and no
Carrier-match window. Instead, the 3 hours is a **weight-hold timeout on the Carrier's
capacity while the Proxy's estimate is awaiting the Buyer's acceptance**:

- While the estimate is pending acceptance (window between 2-b/2-c and 2-d), the Carrier's
  **remaining weight is temporarily reduced by the Proxy Buyer's estimate weight** (a hold).
- If the Buyer does **not approve the Proxy's *latest* estimate within 3 hours**, the hold
  is released and the Carrier's remaining weight becomes **full again**.
- "Latest estimate" ⇒ if the Buyer edits the order and the Proxy **re-estimates**, the
  3-hour clock **resets** against the new estimate (and the hold updates to the new weight).
- When the Buyer **accepts** the estimate within the window, the hold is retained (becomes
  committed) and the flow proceeds to **deposit** (2-d). No extra Carrier confirmation.

---

## Why this matters / what it corrects
- A **TravelPlan is always a Carrier offering** (Carrier Only). There is no
  "proxy-buyer plan" type. Proxy-buying is the admin **Proxy Buyer** responding to an
  Order with an estimate — it is **not** a flow or plan type.
- The only structural difference between the two flows is **carrier-attach timing**:
  - Flow 1: carrier attaches **after** the estimate is accepted (via Queuing Cargo → Offer).
  - Flow 2: carrier is attached **before** the estimate (Buyer ordered against a specific
    queued Carrier), so the Queuing-Cargo / Offer steps (1-c, 1-d) are skipped.

## DECISIONS LOCKED (07-Jul, user) — build these
1. **2-b entry:** "Send Order" on the Queuing Carrier board opens the **full product-order
   form** (products/qty/deadline); the new order is **bound to that carrier** and routed to
   the Proxy Buyer for an estimate. Carrier stays attached through to deposit. Replaces
   today's one-click "attach carrier to existing estimated orders".
2. **Hold start:** **No hold between 2-b and 2-c.** Carrier stays fully Open until the Proxy
   sends the estimate; then hold = the Proxy's estimate weight and the 3h clock starts.
3. **On timeout:** **Keep the order↔carrier binding**; only free the weight. Re-check the
   carrier still has room when the buyer accepts later — if not, block with a message.

### Implementation plan (chunks; each leaves app runnable)
- **A — Binding + entry form:** new `Order.carrier_first_plan` FK (null) + migration. Board
  "Send Order" → renders the **buyer-first product-order form** pre-bound to the plan; on
  submit create a normal buyer-first Products order with `carrier_first_plan=plan`, assign
  proxy, `on_request_submitted`. NO CarrierMatch yet (decision 2).
- **B — Estimate hold + window:** when the Proxy **sends/edits** the estimate, for a bound
  order create/refresh a PENDING `CarrierMatch(order, plan, allocated_kg=estimate weight,
  window=now+carrier_match_window_minutes)`. Re-estimate **resets** the window + amount.
- **C — Estimate-accept branch:** buyer accepts estimate → if `carrier_first_plan` set,
  re-check `plan.carrier_first_remaining_kg` ≥ estimate weight, then spin the TravelerOffer
  from the plan, order → **ACCEPTED** (deposit), mark match ACCEPTED — **skip RESPONDED /
  carrier-selection**. Unbound buyer-first orders still go to RESPONDED (Flow 1 → Queuing
  Cargo, carriers Offer). Insufficient capacity → error ("carrier full").
- **D — Remove old carrier-first surfacing:** delete the buyer `CarrierMatch` Accept/Reject
  UI in `_order_notes.html` RESPONDED block + `Order.live_carrier_matches`; retire the PUSH
  auto-matcher (`run_matcher`/`surface_matches`/`on_estimate_accepted` trigger) and the pull
  `send_carrier_to_buyer` + `match_accept`/`match_reject` views. `CarrierMatch` becomes purely
  the Flow-2 estimate-hold record. (Flow 1 uses the pre-existing `TravelerOffer` offer flow.)

## Gap vs. current build (historical — superseded by DECISIONS LOCKED above)
The user's model above (now CLARIFIED) differs from the as-built Carrier-First code:

| Aspect | Current build | User's model (target) |
| --- | --- | --- |
| Carrier bind | unattached until Buyer Accepts a post-estimate **CarrierMatch** | bound at **2-b** when Buyer orders against the queued Carrier |
| Buyer Accept/Reject **Carrier** step | YES — surfaced on RESPONDED order card w/ Accept/Reject | **NONE** — removed; estimate-accept → deposit directly |
| What the window (180 min / local 5) times | Buyer accepting/rejecting the **CarrierMatch** (post estimate-accept) | Buyer accepting the **Proxy estimate** (pre estimate-accept) |
| Weight hold placed | when the match is surfaced (post estimate-accept) | when the estimate is pending (2-b/2-c → 2-d) |
| Hold amount | allocated match kg | **Proxy's estimate weight** (updates on re-estimate) |
| Timer reset on re-estimate | n/a | **YES** — resets against the latest estimate |
| On timeout | match expires, offer back on board | **hold released, Carrier weight full again** |

**Net change if we adopt the user's model:** remove the `CarrierMatch` Accept/Reject UX from
the RESPONDED block; move the hold + 180-min expiry to trigger at order/estimate time and
tie its release to the Buyer **not accepting the estimate** in time (with reset on
re-estimate). `carrier_match_window_minutes` (180; local 5) is reused as the estimate-accept
hold timeout. See [[project-carrier-first]] "as-built" + matching.py.

### Sub-question still open
- Between **2-b and 2-c** (Buyer has ordered against the Carrier, but the Proxy has not yet
  sent an estimate), is the Carrier's weight already held? By what amount — the Buyer's own
  estimated weight, or nothing until the Proxy's estimate weight exists at 2-c? (The user
  specified the hold amount as "the estimate weight **from Proxy Buyer**", which only exists
  from 2-c onward.)
