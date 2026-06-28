# Phase 8 · Task 23 — Dead-code & schema audit

**Date:** 2026-06-28 · **Branch:** `phase8-dead-code-cleanup` (off `main` @ `7a432ee`)
**Method:** codebase-memory graph (1716 nodes) for orphan candidates + grep cross-check against `urls.py`, `{% url %}`/`reverse`/`redirect`, template `include`/`extends`/`render`. Django dead code is **not** decidable from the call graph alone (views reached via URLconf, methods via templates), so every graph orphan was confirmed against the repo before listing.

## Headline finding
The roadmap assumed a large dead cluster from the abandoned one-to-many "spare baggage" design. **That cluster is mostly NOT dead.** The buyer-owned **cargo** flow (`cargo_only=True`, the `leg_*` lifecycle) is still **user-creatable** — `order_form.html` + `accounts/profile.html` expose the `cargo_only` choice, `views.py:286/350` read it, and `cargo_looking` (rendered on the home board) includes Flow-2 cargo orders. So `leg_*` views, `TravelPlan`, `avail_kg`, `effective_weight_kg`, `remaining_bid_weight_kg`, `total_allocated_weight_kg` are all **live**. Do **not** delete the leg/cargo flow.

What *is* dead is a narrower, well-defined set below.

---

## TIER A — code-dead, high confidence (Task 24, no schema change)

### A1. Orphan view + URL route pairs
No `{% url %}` reference, no `reverse()`/`redirect()`, no internal caller.

| View (`apps/trips/views.py`) | Route (`apps/trips/urls.py`) | Notes |
|---|---|---|
| `offer_estimate_create` (:470) | `offer_estimate_create` (:13) | Superseded by `proxy_estimate`. Valid code but unreferenced. Graph showed a CALLS edge — **false positive** from URL registration `views.offer_estimate_create`. |
| `leg_dropped_off` (:858) | `leg_dropped_off` (:23) | ⚠️ verify body isn't a still-wired sub-step before deleting. |
| `leg_received` (:925) | `leg_received` (:25) | ⚠️ same — likely superseded by `leg_weight_verify`/`leg_arrived`. |
| `request_pickup_select` (:1501) | `request_pickup_select` | Only "reference" is a coincidental migration name `0046_buyrequest_pickup_selected`. |

### A2. Dead templates (never `include`/`extends`/`render`'d)
- `templates/pages/_home_order_table.html`
- `templates/pages/_home_plan_table.html`
- `templates/pages/_home_looking_for_cargo_table.html`
- `templates/trips/offer_form.html`

(False positives excluded: `account/*` rendered by allauth convention, `emails/*` rendered via dynamic `emails/{event}.html` names.)

### A3. Dead home-view "Board 2" logic — `apps/pages/views.py`
`home.html` renders only the proxy table + `cargo_looking`. It never renders Board 2.
- `open_plans_cargo` computation (`:29-36`) and the `looking_for_cargo` context key (`:105`) — computed, passed, never displayed.
- Local `min_remaining` / `MIN_REMAINING_WEIGHT_KG` (`:96`, `:107`) — redundant with the global context processor, and only consumed by the dead templates in A2.
- `apps/pages/context_processors.py:21-38` `MIN_REMAINING_WEIGHT_KG` injection — its **only** consumers are the 3 dead `_home_*` templates (A2). Dead once they go. (NB: the `SiteSettings.min_remaining_weight_kg` *field* stays — still read by `accounts/views.py:549` and `trips/views.py:156`.)

### A4. Dead model method
- `BuyRequest.pending_weight_kg` (`apps/trips/models.py:606`) — defined, never read anywhere.

---

## TIER B — verify before acting
- **`leg_dropped_off` / `leg_received`** (A1): they're orphaned routes *inside an otherwise-live flow*, so read each body in Task 24 to confirm a newer step replaced them (vs. a missing `{% url %}` link that's actually a bug).
- Whether any **other** `leg_*` step lost its UI link the same way (audit the leg flow's template links end-to-end).

## TIER C — data-dead / schema (Task 25) — NOT YET ENUMERATED
No clearly-dead DB column surfaced from the flow analysis (the spare-baggage fields are live). A proper schema audit needs a dedicated **field-by-field read/write pass**: for every `models.*Field`, grep reads across `*.py` + templates; flag fields written-only or referenced solely by Tier-A dead code. Seed candidates: anything read *only* by the A2 dead templates. Defer to a Task 25 sub-pass; back up the DB first, manual deploy (no `seed`).

---

## Tooling note
The codebase-memory Cypher engine in this index has **broken aggregation** (`count()` after `OPTIONAL MATCH`+`WITH` returns a global count, not per-group) and rejects `NOT`/pattern-negation. Graph orphan detection here = export `CALLS` targets + node defs and diff externally; treat every result as a *candidate* pending grep confirmation.
