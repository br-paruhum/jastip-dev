# Flow specs

Hand-authored, **authoritative** state-machine specs for the two goProxyBuy
buyer-first ordering flows. These say what the flow *should* be; the Django code
(`apps/trips/workflow.py`, the `_order_notes.html` template) is what it
*actually does*. They drift unless kept in sync — see "Matching the code" below.

| File | Flow |
|------|------|
| `buyer_first_option1.yaml` | **Option 1 — Buyer sends a *Buy Order*** (a Proxy Buyer sources the goods, a Traveler carries them). |
| `buyer_first_option2.yaml` | Option 2 — Buyer sends a *Cargo Order* (goods already in hand; no proxy, a Traveler carries them). *(to author)* |

## Schema

Each flow is a list of `steps:`. One entry per **state** the order can be in —
i.e. one entry per Notes-tab snapshot. Fields:

- `id` — the step code, e.g. `5A.3`. Matches the `[..]` codes rendered in the
  Notes tab (the per-role suffix B/P/T is dropped from the id; it lives under
  `notes:`).
- `step` — the coarse step number (1–16) from the How-To sheet.
- `actor` — who triggered the transition into this state (`buyer` / `proxy` /
  `traveler` / `admin`).
- `trigger` — the button/action that fires it.
- `order_status` — the `Status` enum **DB value** the Order holds in this state
  (`apps/trips/constants.py`). Must be a real enum value.
- `workflow` — the `apps/trips/workflow.py` function that performs the
  transition (blank if the state is a pure form-fill with no status change).
- `notes` — the Notes-tab copy each role sees now, keyed `buyer` / `proxy` /
  `traveler`, each `{ code, text }`. `code` is the `[..]` anchor (dot form,
  e.g. `2A.2P`); `text` is verbatim from `NotesTabContents_BuyerComeFirst.xlsx`.
  `null` = that role sees nothing / not involved. `"(unchanged)"` = role keeps
  the previous state's note.
- `next` — the `id`s reachable from here.

## Matching the code ("disciplined about matching code")

The spec is only worth trusting if it can't silently drift from the code. Two
cheap checks, run by `scripts/check_flows.py`:

1. **Every note code in the spec exists in the template, and vice-versa.**
   Extract the `[..]` codes from `templates/trips/_order_notes.html`, extract
   the `notes.*.code` values from the YAML, diff the two sets. A mismatch means
   either the spec or the template is stale. (Dot vs dash is normalised —
   template writes `[5A-3B]`, spec writes `5A.3B`.)
2. **Every `order_status` is a real `Status` enum value.**

Run: `python scripts/check_flows.py` — non-zero exit on any divergence. That is
the "discipline": a test, not good intentions.
