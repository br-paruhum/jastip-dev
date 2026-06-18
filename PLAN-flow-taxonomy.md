# Implementation Plan — Full 4-Flow Taxonomy

Status: **PLANNED, not started.** Written 18-Jun-2026. Build phase-by-phase with a
staging test gate between each phase (see §5). Production is **not** touched until an
explicit `./scripts/deploy.sh prd` after the user signs off.

## 1. The model (Option A — one type per post)
Two fixed dimensions. The transaction type is set by the **initiator's post**; the
**responder inherits it** and never chooses a type. No "both" option.

| Dimension | Values | Where it lives |
|-----------|--------|----------------|
| Initiator | Traveler-first / Buyer-first | which entity is posted (TravelPlan vs Order) |
| Transaction type | **Proxy-buying** / **Carrying** | a flag on the post |

| Flow | Initiator post | Type | Responder |
|------|----------------|------|-----------|
| 1 Proxy Buyer    | TravelPlan                   | proxy | Product Buyer orders |
| 2 Carrier        | TravelPlan                   | cargo | Cargo Buyer orders |
| 3 Products Buyer | Order (BuyRequest, plan=None) | proxy | Proxy Buyer offers |
| 4 Cargo Buyer    | Order (BuyRequest, plan=None) | cargo | Carrier offers |

**Live today = 1 & 4** (the diagonal). **New = 2 & 3.**

## 2. Data model
- **`TravelPlan.carrier_only`** already exists → becomes the strict type flag.
  Change the form choice from *"Proxy Buyer + Carrier / Carrier Only"* to
  **"Proxy Buyer" / "Carrier"** (exactly one).
- **`BuyRequest.cargo_only`** — NEW `BooleanField(default=False)`, the buyer-side mirror.
  `False` = Products Buyer, `True` = Cargo Buyer. Only meaningful when `plan_id is None`
  (buyer-first); for plan-first requests the type is inherited from the linked plan.
  → one migration. **Decision: field name `cargo_only` (mirrors `carrier_only`).**
- **Single source of truth for labels** — model properties so no template hardcodes a
  badge again:
  - `TravelPlan.type_label` → "Proxy Buyer" / "Carrier"
  - `BuyRequest.actor_label` / `.counterparty_label` → derived from `cargo_only`
    (buyer-first) or the linked plan (plan-first)

## 3. The form difference
| | Proxy-buying post | Carrying post |
|---|---|---|
| Buyer provides | Items to **buy** + target prices | Contents declared for customs + weight |
| Who buys | Traveler purchases abroad | Nobody — buyer already has the goods |
| Handover | Traveler buys → carries → delivers | Buyer **drops off** → traveler carries → delivers |
| Money | Deposit → actual purchase cost recorded | Deposit → carry fee only |
| Lifecycle reused | today's **request purchase** flow | today's **leg drop-off** flow |

Crux: **Flow 2 = carry lifecycle on a traveler-first post; Flow 3 = purchase lifecycle
on a buyer-first post.** Both recombine lifecycles that already exist — real work, not
just labels.

## 4. Enforcement + messaging (labelled AND enforced)
Three layers, so a mismatch is impossible *and* explained:
1. **Adaptive forms** — the Order / Make-an-Offer button opens the form matching the
   post's type, so a responder normally can't mismatch.
2. **Browse clarity** — every plan/order in the lists carries a type badge; an action
   that doesn't fit shows the rejection copy instead of proceeding, e.g.:
   - *"This traveler is a Proxy Buyer who shops for items abroad. To send a parcel you
     already have, find a Carrier plan instead."*
   - *"This order needs a Proxy Buyer to purchase the items. As a Carrier you can only
     respond to Cargo orders."*
3. **Server-side validation** — create-order / create-offer views reject incompatible
   submissions with the same message (backstop against stale forms).

## 5. Build order (phased; staging test gate between each)
Each phase: migration (if any) → templates → views → enforcement → tests → deploy to
**staging** → **user tests & confirms** → next phase. Most that can ever need redoing is
one phase.

- **Phase 0 — Foundation (no behaviour change).** Add `cargo_only` + backfill; add label
  properties; swap all badges/headers to derived labels; tighten traveler form to one
  type. Two live flows look identical but are now properly typed. *Low risk.*
- **Phase 1 — Enforcement scaffolding.** Type badges in browse lists, adaptive form
  routing, server validation + messages — on the existing two flows first.
- **Phase 2 — Flow 2 (Carrier, traveler-first).** Carry/drop-off lifecycle on
  traveler-first orders (reuse leg mechanics). *Medium-heavy.*
- **Phase 3 — Flow 3 (Products Buyer, buyer-first).** Purchase lifecycle + customs
  invoice on buyer-first legs (reuse request-purchase mechanics). *Heaviest — touches
  payments and customs.*

## 6. Data backfill (in the Phase 0 migration)
- Existing buyer-first orders → `cargo_only = True` (all current buyer-first = Cargo).
- Existing carrier-only plans stay Carriers; all other plans = Proxy Buyer.

## 7. Testing (on staging, per phase)
Per flow: post → respond → deposit → fulfil → clear, plus the 4 mismatch-rejection
cases. Fixtures: one order of each of the 4 types.

## 8. Decisions locked
1. Field name: `BuyRequest.cargo_only` (boolean, mirrors `carrier_only`). ✅
2. Backfill current data as in §6. ✅
3. Scope: build **Phase 0 first**, stop for staging test, then proceed phase-by-phase. ✅
4. Production untouched until explicit prod deploy after sign-off. ✅
