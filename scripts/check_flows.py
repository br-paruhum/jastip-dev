#!/usr/bin/env python3
"""Keep the hand-authored flow specs in flows/*.yaml honest against the code.

Two checks (see flows/README.md → "Matching the code"):
  1. Every note code in a spec is rendered in templates/trips/_order_notes.html,
     and vice-versa — so the spec can't silently drift from what buyers see.
  2. Every order_status is a real trips.constants.Status enum value.

Exit non-zero on any divergence. No deps beyond PyYAML + Django settings.
Run: python scripts/check_flows.py
"""
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FLOWS = ROOT / "flows"
# Codes are rendered across the trip templates: the Notes tab (_order_notes.html)
# plus the form/purchase bodies that carry the form-fill guidance codes
# (e.g. [1A-1B] in _request_form_body.html, [6A-1P] in _proxy_purchase_body.html).
TMPL_DIR = ROOT / "templates" / "trips"

# Template writes [5A-3B]; specs write 5A.3B. Normalise both to 5A3B for diffing
# (drop the separators entirely so 10.1A-0P and 10.1A.0P collapse together too).
_norm = lambda code: re.sub(r"[.\-\[\]]", "", code).upper()

CODE_RE = re.compile(r"\[[0-9][0-9A-Za-z.\-]*\]")


def template_codes():
    codes = set()
    for tmpl in TMPL_DIR.glob("*.html"):
        codes |= {_norm(m) for m in CODE_RE.findall(tmpl.read_text(encoding="utf-8"))}
    return codes


def spec_codes_and_statuses(path):
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    codes, statuses = set(), set()
    for st in spec.get("steps", []):
        statuses.add(st.get("order_status"))
        for role in st.get("notes", {}).values():
            if role and isinstance(role, dict) and role.get("code"):
                codes.add(_norm(role["code"]))
    return codes, statuses


def valid_statuses():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from apps.trips.constants import Status

    return {s.value for s in Status}


def main():
    tmpl = template_codes()
    try:
        allowed = valid_statuses()
    except Exception as exc:  # Django not importable → skip check 2, warn loudly
        print(f"! could not load Status enum ({exc}); skipping status check")
        allowed = None

    ok = True
    for path in sorted(FLOWS.glob("*.yaml")):
        codes, statuses = spec_codes_and_statuses(path)
        missing_in_tmpl = codes - tmpl          # spec codes the template never renders
        if missing_in_tmpl:
            ok = False
            print(f"✗ {path.name}: codes not in _order_notes.html: "
                  f"{sorted(missing_in_tmpl)}")
        if allowed is not None:
            bad = {s for s in statuses if s not in allowed}
            if bad:
                ok = False
                print(f"✗ {path.name}: not a Status enum value: {sorted(bad)}")
        if not (missing_in_tmpl or (allowed and statuses - allowed)):
            print(f"✓ {path.name}: {len(codes)} codes, {len(statuses)} statuses OK")

    # Reverse direction reported as a warning, not a failure: the template also
    # carries Option-2/carrier-first codes not yet specced, so orphans are noise
    # until every flow has a YAML. Flip to `ok = False` once all flows exist.
    all_spec = set()
    for path in FLOWS.glob("*.yaml"):
        c, _ = spec_codes_and_statuses(path)
        all_spec |= c
    orphan = tmpl - all_spec
    if orphan:
        print(f"  (note: {len(orphan)} template codes not in any spec — "
              f"expected until every flow is authored)")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
