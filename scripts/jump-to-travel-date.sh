#!/usr/bin/env bash
#
# Usage: bash jump-to-travel-date.sh <ORDER-REF>
#   e.g. bash jump-to-travel-date.sh PRX-260623JEB
#
# Moves the order's live carrier-offer travel_date to TODAY so time-gated
# actions unlock (the carrier can mark arrival, then drive the reship flow).
# The original travel_date is backed up to scripts/.date-backup/<REF>.txt so
# jump-back-to-current-date.sh can restore it. (Proxy buyer-first orders keep
# the travel date on the carrier offer; this script targets that.)
#
set -euo pipefail

REF="${1:?Usage: bash jump-to-travel-date.sh <ORDER-REF>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(dirname "$SCRIPT_DIR")"
BAKDIR="$SCRIPT_DIR/.date-backup"
mkdir -p "$BAKDIR"
cd "$APP"

REF="$REF" BAKDIR="$BAKDIR" "$APP/.venv/bin/python" manage.py shell <<'PY'
import os, datetime
from apps.trips.models import BuyRequest
from apps.trips.constants import OfferStatus

ref = os.environ["REF"]
bakdir = os.environ["BAKDIR"]

o = BuyRequest.objects.filter(reference=ref).first()
if not o:
    raise SystemExit(f"ERROR: no order with reference {ref}")
offer = (o.traveler_offers
          .filter(offer_status__in=[OfferStatus.PENDING, OfferStatus.SELECTED])
          .order_by("id").first())
if not offer:
    raise SystemExit(f"ERROR: no live carrier offer on {ref}")

bakpath = os.path.join(bakdir, f"{ref}.txt")
# Only back up once, so repeated runs never clobber the true original.
if not os.path.exists(bakpath):
    with open(bakpath, "w") as f:
        f.write(offer.travel_date.isoformat())

old = offer.travel_date
offer.travel_date = datetime.date.today()
offer.save(update_fields=["travel_date"])
print(f"{ref}: travel_date {old} -> {offer.travel_date} (original backed up)")
PY
