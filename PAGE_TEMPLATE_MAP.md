# Page → Template Mapping

Find the HTML file behind any page or form. Updated for the **Proxy Buyer redesign**
(Flow‑1 admin‑approved proxy + per‑leg carrier; Flow‑2/3 cargo).

How routing works:
- URL → view function → `render(request, "<template>", ...)`.
- Most "pages" extend `templates/base.html`.
- The **dashboard** (`/u/dashboard/`) is a single page (`accounts/profile.html`) that
  swaps **panels** client‑side; each panel `{% include %}`s a `_*_body.html` partial.
  To edit a dashboard sub‑screen, edit its **partial**, not `profile.html`.
- `_*.html` files (underscore prefix) are partials included by other templates.

All template paths are under `templates/`.

---

## Quick lookup — proxy test-walkthrough URLs

Ctrl‑F the URL you're on. Dedup'd by pattern (`<id>` = order/offer id). Every
`/u/dashboard/…` page is the shell `accounts/profile.html`; edit the partial below.

| URL | User | Template to edit |
|---|---|---|
| `/trips/orders/new/?proxy=<id>` | Buyer | `trips/order_form.html` |
| `/u/dashboard/?order=<id>#order-detail` | Buyer / Proxy | `trips/_order_detail_body.html` · Messages → `trips/_chat_thread.html` |
| `/u/dashboard/?order=<id>#my-orders` | Buyer | rows `accounts/_orders_table.html`; panel shell in `accounts/profile.html` |
| `/u/dashboard/#proxy-orders` | Proxy | `accounts/profile.html` (inline "My Proxy Buying" table) |
| `/u/dashboard/?estimate=<id>#estimate-form` | Proxy | `trips/_proxy_estimate_body.html` |
| `/u/dashboard/?package_ready=<id>#package-ready` | Proxy | `trips/_proxy_purchase_body.html` |
| `/u/dashboard/?offer=<id>#offer-form` | Carrier | `accounts/profile.html` (inline "Place an Offer" form) |
| `/u/dashboard/?offer=<id>#offer-detail` | Carrier | `trips/_offer_detail_body.html` · Messages → `trips/_chat_thread.html` |
| `/u/dashboard/?offer=<id>#travel-plans` | Carrier | rows `accounts/_travel_table.html`; panel shell in `accounts/profile.html` |
| `/u/dashboard/#pending-disbursements` | Superuser | `accounts/profile.html` (inline panel) |
| `/trips/requests/<id>/invoice/` | Buyer | `trips/order_invoice_print.html` |

> `_order_detail_body.html` text branches by `bf_order.status` and `is_order_buyer`/
> `is_order_proxy`; `_offer_detail_body.html` branches by `leg_offer.order.status`.

---

## Public / marketing pages

| Page | URL name | View | Template |
|---|---|---|---|
| Home (landing, all boards) | `pages:home` | `apps/pages/views.py:home` | `pages/home.html` |
| ↳ Proxy Buyers board (Country/**City**/Margin) | — | — | `pages/_home_proxy_table.html` |
| ↳ "Looking for Carrier" (cargo) board | — | — | `pages/_home_cargo_looking_table.html` |
| ↳ "Looking for Cargo Buyer" (plans) board | — | — | `pages/_home_looking_for_cargo_table.html` |
| ↳ generic order/plan board partials | — | — | `pages/_home_order_table.html`, `pages/_home_plan_table.html` |
| How‑To | `pages:how_to` | `apps/pages/views.py:how_to` | `pages/page.html` (DB‑seeded body) |
| Generic CMS page (`/p/<slug>/`) | `pages:page` | `apps/pages/views.py:page_detail` | `pages/page.html` |
| Contact | `pages:contact` | `apps/pages/views.py:contact` | `pages/contact.html` |

> How‑To / CMS page **bodies** are seeded into the DB (SitePage), not in the template —
> edit the seed source and run `manage.py seed`. The template is just the shell.

---

## Accounts / auth

| Page | URL name | View | Template |
|---|---|---|---|
| Choose role | `accounts:choose_role` | `apps/accounts/views.py:choose_role` | `accounts/choose_role.html` |
| Sign up / login / password reset | allauth (`accounts/...`) | allauth | `templates/account/*.html` |
| **Dashboard** (hub) | `accounts:profile` | `apps/accounts/views.py:profile` | `accounts/profile.html` |

---

## Dashboard panels (all inside `accounts/profile.html`)

The dashboard shows one panel at a time (`data-panel="…"`). Edit the partial listed,
not `profile.html` itself (unless changing the shell/layout/JS).

| Panel (`data-panel`) | What it is | Partial to edit |
|---|---|---|
| `profile` | Profile + credentials fold | `accounts/profile.html` (inline) |
| `travel-plans` | My Travel Plans table | `accounts/_travel_table.html` |
| `my-orders` | My Orders (Proxy Buying / Carrier Only) | `accounts/_orders_table.html` |
| `order-detail` | Buyer‑first order detail | `trips/_order_detail_body.html` |
| `proxy-orders` | "My Proxy Buying" queue (proxy person) | `accounts/profile.html` (inline table) |
| `estimate-form` | Proxy sends estimate | `trips/_proxy_estimate_body.html` |
| `package-ready` | Proxy records actual purchase costs | `trips/_proxy_purchase_body.html` |
| `plan-detail` | Embedded travel‑plan view | `trips/_plan_detail_body.html` |
| `offer-detail` | Carrier/traveler offer detail | `trips/_offer_detail_body.html` |
| `review-order` | Traveler reviews request | `trips/_request_review_body.html` |
| `purchase-order` | Traveler records purchase | `trips/_request_purchase_body.html` |
| `receive-order` | Mark received | `trips/_request_receive_body.html` |
| `arrive-order` | Mark arrived | `trips/_request_arrive_body.html` |
| `reship-cost-order` | Reship cost entry | `trips/_request_reship_cost_body.html` |
| `reship-order` | Reship action | `trips/_request_reship_body.html` |
| `order-form` | Edit order (embedded) | `trips/_request_form_body.html` |
| `offer-form` | Place/edit carry offer (embedded) | `accounts/profile.html` (inline form) |
| `pending-disbursements` | Pending Disbursement queue (superuser only) | `accounts/profile.html` (inline panel) |
| `reset-password` | Change password | `accounts/profile.html` (inline) |
| chat thread (in detail panels) | — | `trips/_chat_thread.html` |
| status badges (everywhere) | — | `trips/_status_badge.html` |

---

## Travel plans (traveler)

| Page / form | URL name | View | Template |
|---|---|---|---|
| New travel plan | `trips:plan_create` | `apps/trips/views.py:plan_create` | `trips/plan_form.html` |
| Edit travel plan | `trips:plan_edit` | `plan_edit` | `trips/plan_form.html` |
| Travel plan detail (standalone) | `trips:plan_detail` | `plan_detail` | `trips/plan_detail.html` → `trips/_plan_detail_body.html` |
| Cancel plan | `trips:plan_cancel` | `plan_cancel` | (redirect, no template) |

---

## Orders (Flow‑1: proxy buying & buyer‑first)

| Page / form | URL name | View | Template |
|---|---|---|---|
| New order (optionally `?proxy=<id>`) | `trips:order_create` | `order_create` | `trips/order_form.html` |
| Edit order | `trips:order_edit` | `order_edit` | `trips/order_form.html` |
| Carry offer on order | `trips:offer_create` | `offer_create` | (form in `_order_detail_body` / redirect) |
| Proxy estimate (create) | `trips:offer_estimate_create` | `offer_estimate_create` | `trips/_proxy_estimate_body.html` (panel) |
| Proxy estimate | `trips:proxy_estimate` | `proxy_estimate` | `trips/_proxy_estimate_body.html` |
| Proxy purchase (actual costs) | `trips:proxy_purchase` | `proxy_purchase` | `trips/_proxy_purchase_body.html` |
| Accept / reject / deposit | `trips:order_accept` / `order_reject` / `order_deposit_pay` | resp. views | (actions / redirects) |
| Assign items to legs | `trips:order_assign_items` | `order_assign_items` | (action / redirect) |
| Edit a carry offer | `trips:offer_edit` | `offer_edit` | `trips/offer_edit.html` |

---

## Legs / carrier lifecycle (per‑offer, Flow‑2/3)

These are mostly **action endpoints** (POST → redirect back to the order/offer detail
panel). They have no standalone template; the UI lives in
`trips/_order_detail_body.html` and `trips/_offer_detail_body.html`.

`trips:offer_select`, `leg_deposit_pay`, `leg_dropped_off`, `leg_weight_verify`,
`leg_received`, `leg_arrived`, `leg_balance_pay`, `leg_refund_bank`,
`leg_choose_fulfillment`, `leg_reship_cost`, `leg_reship_proof`, `leg_reship_ship`,
`leg_clear`, `offer_withdraw`.

Shared partials: `trips/_pay_form.html`, `trips/_reship_proof_form.html`.

---

## Requests (traveler‑side of a plan order)

| Page / form | URL name | View | Template |
|---|---|---|---|
| New request on a plan | `trips:request_create` | `request_create` | `trips/request_form.html` |
| Edit request | `trips:request_edit` | `request_edit` | `trips/request_form.html` |
| Request detail (standalone) | `trips:request_detail` | `request_detail` | `trips/request_detail.html` → `trips/_request_detail_body.html` |
| Review | `trips:request_review` | `request_review` | `trips/request_review.html` |
| Purchase | `trips:request_purchase` | `request_purchase` | `trips/request_purchase.html` |
| Arrive | `trips:request_arrive` | `request_arrive` | `trips/request_arrive.html` |
| Receive / pay / clear / message / refund | `request_receive` / `request_pay` / `request_clear` / `request_message` / `request_refund_bank` | resp. views | (actions / redirects) |
| Reship request / cost / ship / proof | `request_reship_request` / `request_reship_cost` / `request_reship` / `request_reship_proof` | resp. views | (actions / partials) |

---

## Print / invoice & misc

| Page | URL name | View | Template |
|---|---|---|---|
| Order invoice (print) | `trips:request_invoice` | `request_invoice` | `trips/order_invoice_print.html` |
| Customs invoice (print) | `trips:request_customs_invoice` | `request_customs_invoice` | `trips/customs_invoice_print.html` |
| FX rates (Kurs) | `trips:fxrate` | `kurs` | `trips/kurs.html` |

---

## Email templates

Workflow emails live in `templates/emails/*.html` (e.g. `proxy_new_order.html`,
`proxy_offer_accepted.html`, `proxy_first_disbursed.html`, `payout_released.html`,
`deposit_paid.html`, `customs_invoice.html`, `chat_message.html`, `closed.html`),
wrapped by `templates/email_base.html`.

---

## Shared shell / layout

| Piece | Template |
|---|---|
| Base layout (nav, footer) | `base.html` |
| Ad sidebar | `_ad_sidebar.html` |
| Cookie consent | `_cookie_consent.html` |
