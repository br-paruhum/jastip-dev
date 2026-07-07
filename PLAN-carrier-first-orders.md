# Plan — Carrier-First Orders (Queuing Carrier + Match-to-Buyer)

Status: **IMPLEMENTED (v1) on branch `carrier-first-flow` · 07-Jul-2026** — this doc is
reconciled to the as-built code. Two deltas from the original design emerged while reading
the existing flow and are folded in below (marked **AS-BUILT**).

Companion to `PLAN-buyer-first-orders.md` and `PLAN-flow-taxonomy.md`. Nothing in
the existing plan-first or buyer-first flows changes; this adds a third entry path
that **reuses the Flow-1 Products (buyer-first) lifecycle** downstream.

**AS-BUILT deltas (read these first):**
1. On accept the order does **not** gain `order.plan` (that would break every
   Flow-1 view filtering `plan__isnull=True`). Instead a PENDING `TravelerOffer` is
   spun up from the plan and routed through the existing `order_accept` path — so
   the order stays on buyer-first rails and the whole downstream is reused unchanged.
2. Locked-threshold reuses the existing `SiteSettings.min_remaining_weight_kg`
   (already "below this → Full, block new orders"); no new threshold field was added.

Code: `apps/trips/matching.py` (service), `CarrierMatch` model + `TravelPlan`
capacity props in `apps/trips/models.py`, board/actions in `apps/trips/views.py`
(`queuing_carrier_board`, `match_accept`, `match_reject`, `send_order_to_carrier`),
`match_carriers` management command, tests in `CarrierFirstMatchingTests`.

---

## 1. Goal

Add a third way to start a transaction: **Carrier-First**.

- **plan-first** (today): a carrier posts a `TravelPlan`; buyers browse and order
  against a specific plan.
- **buyer-first** (today): a buyer posts a route order with no carrier; carriers
  respond with `TravelerOffer`s; the buyer selects one (or splits across several).
- **carrier-first** (new): a carrier posts spare capacity **and it sits queued**;
  a buyer posts a route order that the **proxy estimates first**; the system then
  **surfaces every matching queued carrier on the buyer's page** and the buyer
  **selects one** — exactly like today's offer-selection UX, except the carriers
  were waiting in a queue rather than responding to this specific order.

The insight that keeps this small: **"a carrier posts first, and its capacity is
shared across many buyers" already exists** — it is the plan-first flow, and
`TravelPlan` already tracks shared remaining capacity
(`active_requests_with_capacity`, `apps/trips/models.py:149`;
`utilized_weight_kg` / `remaining`, `apps/trips/models.py:236`). What is genuinely
new is a **matching + notification + hold layer** on top, plus a **Queuing Carrier
board** UI.

---

## 2. Design decisions (locked with user, 07-Jul-2026)

1. **Reuse `TravelPlan`** as the Queuing Carrier record — no new carrier model.
   The "Queuing Carrier" board is a filtered view of open `TravelPlan`s.
   **AS-BUILT:** on accept the order keeps `plan = null` and a PENDING
   `TravelerOffer` is created from the plan, then routed through the existing
   `order_accept` path — reusing the Flow-1 Products downstream without disturbing
   the `plan__isnull=True` view filters. (`order.plan = plan` was the original idea
   but breaks those filters.) `CarrierMatch` is the sole capacity record.
2. **Open vs Locked** is derived from *effective remaining* capacity:
   **Locked when effective remaining < the shared `min_remaining_weight_kg`
   threshold** (default 0.99 kg — the same "Full" threshold plan-first already
   uses), Open otherwise.
3. **Soft-hold during the accept window** — a pending match reserves its weight so
   other buyers see an honest remaining figure. A hold is **not** a lock: it only
   decrements the displayed remaining; status stays Open until remaining < 1 kg.
4. **Thresholds are admin-editable** (`SiteSettings`), not hardcoded: carrier-match
   lead-time (days) and accept-window (hours).
5. **Buyer chooses** among matching carriers — the system never auto-picks by
   price or travel date. Unselected carriers **do not disappear**; they simply
   remain on the Queuing board for other buyers.
6. **Estimate = weight only, never shipping cost.** The proxy's estimate carries
   Product Cost, Total Estimated Weight, and Margin. The shipping cost *always*
   comes from the carrier's rate — so it is only known once a carrier is selected.
   This matches how the platform already works; there is no new "two-stage
   acceptance" concept.

---

## 3. Terminology / board mapping

| Brainstorm term | Existing / new artifact |
|---|---|
| "Queuing Carrier" board (Route, Travel Date, Weight, Rate, Status, Action) | Filtered view of open `TravelPlan`s (`apps/trips/models.py:70`) |
| Weight (remaining) | `effective_remaining(plan)` — see §5 |
| Status: Open / Locked | Derived from `effective_remaining` (Open ≥ 1 kg, else Locked) |
| Action: Send Order | Buyer posts / attaches a route order to this carrier (§6, pull path) |
| Action: Closed | Owner-carrier closes their queued capacity (existing plan cancel/close) |

---

## 4. End-to-end flow (mirrors the user's scenario 1–6)

```
(1) Carrier posts TravelPlan ───────────────► Queuing Carrier board (Open)
                                                     │
(2) Buyer posts route order (plan = null) ──► (3) Proxy estimate
        (Products or Cargo, route + deadline)         (Product Cost, Total Est.
                                                        Weight, Margin) → ESTIMATE_SENT
                                                     │
(4) Buyer accepts estimate ─────────────────► ACCEPTED
                                                     │
                    ┌────────────────────── matching gate (§7) ──────────────────┐
              PUSH: system surfaces EVERY qualifying queued carrier on the        PULL: buyer opens the
              buyer's page + WhatsApp/email; each creates a pending hold (§5).     Queuing board and clicks
              Accept window = SiteSettings hours (default 3).                      "Send Order" (scenario 6.d).
                                                     │
(5/6) Buyer SELECTS one carrier ────────────► that hold becomes a permanent
        (the rest are released;                allocation; order.plan = plan;
         unselected carriers stay on board)     order continues as normal
                                                plan-first:
                                                DEPOSIT_PAID → ITEMS_PURCHASED →
                                                handover → PACKAGE_ARRIVED →
                                                pickup / reship → CLEAR → CLOSED
```

Failure / no-action branches:
- Buyer **ignores** all surfaced offers past the window → every pending hold
  expires, the offers drop off the buyer's page, and **no carrier weight changes**
  (scenario 6.c). The order stays ACCEPTED and can be re-matched later.
- Buyer **rejects** a specific surfaced offer → that one hold releases; the rest
  stay live until the window closes.

---

## 5. Capacity & the hold ledger

One formula governs the whole board:

**AS-BUILT** (`TravelPlan.carrier_first_remaining_kg`):

```
effective_remaining(carrier) =
      total_capacity (TravelPlan.available_weight_kg)
    − plan-first orders          (utilized_weight_kg — buyers who set order.plan)
    − Σ accepted CarrierMatch    (CarrierMatch.status = accepted)
    − Σ pending CarrierMatch     (CarrierMatch.status = pending)
```

The two CarrierMatch terms are `TravelPlan.held_hold_kg` (`HELD_MATCH_STATUSES =
{pending, accepted}`). A carrier-first accepted match never sets `order.plan`, so it
is **not** in `utilized_weight_kg` — no double counting; the ledger is its only record.

- The board displays `effective_remaining`. **Open** while ≥ `min_remaining_weight_kg`
  (default 0.99 kg), **Locked** below (`TravelPlan.is_queue_locked`).
- A hold decrements the *displayed* remaining only; it does not lock the carrier
  unless remaining actually falls below the threshold.

Both multi-party cases fall out of the same ledger:

- **One buyer, several carriers** (user's "5 kg × 2"): the buyer is shown carriers
  X and Y for a 5 kg order → a 5 kg hold on **each**. The buyer picks X → Y's 5 kg
  hold is released → only 5 kg is ever consumed.
- **One carrier, several buyers**: two 5 kg buyers see the same 20 kg carrier → two
  holds → any third buyer sees "10 kg remaining · Open", never a stale "20 kg full".
  If both accept, both are genuinely served (10 kg consumed, 10 kg remains); if one
  lapses, its hold releases.

**Guard-rail (prevents overselling small carriers):** a carrier is only surfaced to
a buyer when `effective_remaining` — *after* subtracting holds already out — is
≥ that buyer's Total Estimated Weight. A 6 kg carrier already holding 5 kg for one
buyer will not be offered to a second 5 kg buyer.

**Hold lifecycle:**
- created `pending` when a carrier is surfaced to a buyer (push) or the buyer clicks
  Send Order (pull);
- → `accepted` when the buyer selects that carrier (weight becomes a permanent
  allocation; **AS-BUILT:** a PENDING `TravelerOffer` is created from the plan and
  the order goes ACCEPTED via `order_accept` — `order.plan` stays null);
- → `expired` when the accept window lapses (weight returns to remaining);
- → `rejected` when the buyer dismisses that carrier, or selects a different one
  (weight returns to remaining).

---

## 6. New model — `CarrierMatch`

The mirror of `TravelerOffer`, but system→buyer instead of carrier→order. It also
serves as the hold ledger (§5). It is deliberately thin: no leg lifecycle — on accept
it links to the `TravelerOffer` it spawns and the existing machinery takes over.

| Field | Type | Notes |
|---|---|---|
| `order` | FK → Order | The buyer-first order being matched (`plan` stays null throughout) |
| `plan` | FK → TravelPlan | The queued carrier offered |
| `offer` | FK → TravelerOffer (null) | **AS-BUILT:** the PENDING offer created on accept (drives the lifecycle); null while pending/expired/rejected |
| `allocated_kg` | Decimal | Held/allocated weight = order's Total Estimated Weight at surface time |
| `status` | choices | `pending / accepted / expired / rejected` |
| `offered_at` | DateTime | Start of the accept window |
| `window_expires_at` | DateTime | `offered_at + SiteSettings.carrier_match_window_hours` |
| `source` | choices | `push` (auto-surfaced) / `pull` (buyer clicked Send Order) — for analytics |
| `responded_at` | DateTime | When accepted/rejected (null while pending/expired) |
| `created_at` / `updated_at` | DateTime | Auto |

Indexes: `(order, status)`, `(plan, status)` — both hot paths for the ledger sum.

Constraint: at most one `accepted` CarrierMatch per order (an order attaches to
exactly one carrier; partial-split across carriers is a buyer-first feature and out
of scope here).

---

## 7. Matching gate (scenario step 5)

An open `TravelPlan` matches an `ACCEPTED` order when **all** of:

1. **Route** equal — `from_city`+`from_country` and `to_city`+`to_country`.
2. **Deadline** — `plan.travel_date <= order.max_acceptable_date`
   (the carrier arrives before the buyer's deadline).
3. **Lead time** — `plan.travel_date >= today + SiteSettings.carrier_match_lead_days`
   (default 3) — enough runway for the proxy to purchase and the goods to reach the
   carrier before departure.
4. **Capacity** — `effective_remaining(plan) >= order.estimated_weight_kg`
   (the guard-rail from §5).

Open question to confirm at build time: whether condition 2 is `<=` or strict `<`,
and whether the flow is Products-only or also allows Cargo (`order.cargo_only`)
carrier-first orders. Default assumption: `<=`, both order kinds allowed.

---

## 8. Notifications

On surfacing (push), notify the buyer via **WhatsApp and email** that one or more
carriers are available for their order, with a link to the selection page and the
window deadline. Reuse the existing WhatsApp bot + email plumbing. No new
notification to the carrier at surface time (their capacity is only tentatively
held); notify the carrier on **accept** (a real order is now attached).

---

## 9. Scheduled jobs (reuse the `close_overdue_plans` cron pattern)

**AS-BUILT:** one command `python manage.py match_carriers` runs both, expiry first:

1. **Expirer** (`matching.expire_stale_matches`) — flip `pending` `CarrierMatch` rows
   past `window_expires_at` to `expired`; their held weight returns to
   `effective_remaining` automatically (a sum over live rows — nothing else to update).
2. **Matcher** (`matching.run_matcher`) — for each RESPONDED Products order with no live
   pending match, find qualifying plans (§7), create `pending` `CarrierMatch` rows, and
   notify the buyer. Also runs inline on `estimate_accept` (via
   `workflow.on_estimate_accepted`) for immediacy. Flags: `--dry-run`, `--quiet`.
   Suggested cadence: every ~10 min.

---

## 10. `SiteSettings` additions

Alongside the existing `platform_fee_min_idr` and `min_remaining_weight_kg`
(admin-editable), **AS-BUILT** added exactly two fields:

| Field | Default | Notes |
|---|---|---|
| `carrier_match_lead_days` | `3` | Minimum days between today and the carrier's travel date to qualify |
| `carrier_match_window_hours` | `3` | How long a surfaced carrier offer stays live on the buyer's page |

The Locked threshold **reuses the existing `min_remaining_weight_kg`** (default 0.99 kg)
— no new field.

---

## 11. Downstream reuse (why this stays small)

**AS-BUILT:** on accept, `matching.accept_match` creates a PENDING `TravelerOffer` from
the plan and runs the same steps as `order_accept` (order → `ACCEPTED`, competing offers
declined, `workflow.on_proxy_offer_accepted` fired). The order stays on Flow-1 Products
rails (`plan = null`), so from here **nothing new is needed** — the existing lifecycle
covers deposit, proxy purchase + actual cost, handover to carrier, arrival + customs,
pickup/reship, clear, and close. Shipping cost on the invoice comes from the offer's
`ask_cost_per_kg` (= `plan.shipment_cost_per_kg`) × weight, exactly as Flow-1 already
computes it. The plan's queue capacity is decremented via the `CarrierMatch` ledger, not
`order.plan`.

---

## 12. Out of scope (explicit)

- **Partial split across carriers** for a single carrier-first order — that is a
  buyer-first feature (`partial_allowed` + legs). Here, one order → one carrier.
- Any change to plan-first or buyer-first intake, estimation, or lifecycle.

---

## 13. Implementation checklist (non-binding sketch)

- [ ] `CarrierMatch` model + migration (`apps/trips/models.py`, `OfferSource`/status
      TextChoices in `apps/trips/constants.py`).
- [ ] `effective_remaining` on `TravelPlan` (extend the tally at
      `apps/trips/models.py:236`) + `is_locked` / board status label.
- [ ] `SiteSettings` fields + admin (mirror `platform_fee_min_idr`).
- [ ] Queuing Carrier board view + template (filtered open plans, Open/Locked,
      "Send Order" action).
- [ ] Buyer selection surface (reuse the `offer_select` UX pattern,
      `apps/trips/urls.py:22`) driving `CarrierMatch` accept/reject.
- [ ] Matcher + Expirer management commands / cron (pattern: `close_overdue_plans`).
- [ ] WhatsApp + email notification on surface; carrier notification on accept.
- [ ] `estimate_accept` hook to trigger an immediate match pass.
- [ ] Tests: ledger math (holds/expire/accept), guard-rail, Open↔Locked boundary,
      window expiry, one-accepted-match constraint.
