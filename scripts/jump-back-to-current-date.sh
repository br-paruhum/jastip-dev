#!/usr/bin/env bash
#
# Usage: bash jump-back-to-current-date.sh <ORDER-REF>
#   e.g. bash jump-back-to-current-date.sh PRX-260623JEB
#
# Restores the order's carrier-offer travel_date to the value that
# jump-to-travel-date.sh backed up (scripts/.date-backup/<REF>.txt).
#
set -euo pipefail

REF="${1:?Usage: bash jump-back-to-current-date.sh <ORDER-REF>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(dirname "$SCRIPT_DIR")"
BAKDIR="$SCRIPT_DIR/.date-backup"
cd "$APP"

REF="$REF" BAKDIR="$BAKDIR" "$APP/.venv/bin/python" manage.py shell <<'PY'
import os, datetime
from apps.trips.models import BuyRequest
from apps.trips.constants import OfferStatus

ref = os.environ["REF"]
bakdir = os.environ["BAKDIR"]

bakpath = os.path.join(bakdir, f"{ref}.txt")
if not os.path.exists(bakpath):
    raise SystemExit(f"ERROR: no backup for {ref} (run jump-to-travel-date.sh first)")
with open(bakpath) as f:
    orig = datetime.date.fromisoformat(f.read().strip())

o = BuyRequest.objects.filter(reference=ref).first()
if not o:
    raise SystemExit(f"ERROR: no order with reference {ref}")
offer = (o.traveler_offers
          .filter(offer_status__in=[OfferStatus.PENDING, OfferStatus.SELECTED])
          .order_by("id").first())
if not offer:
    raise SystemExit(f"ERROR: no live carrier offer on {ref}")

old = offer.travel_date
offer.travel_date = orig
offer.save(update_fields=["travel_date"])
print(f"{ref}: travel_date {old} -> {orig} (restored)")
PY
