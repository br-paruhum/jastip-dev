"""Flow-1 over-booking guard: when a traveler offers to carry a proxy order, the
order's weight is RESERVED against the traveler's spare-baggage plan so other
buyers can't grab it. If the buyer ignores the offer for OFFER_DECISION_HOURS
(24h from when they were notified = the offer's creation), lapse it:

  - withdraw the offer (releases the FCFS lock so other travelers can offer),
  - free the reservation on the spawned plan (its full capacity becomes
    available to other buyers again),
  - leave the order at RESPONDED so it returns to the "Looking for Carrier"
    board with its estimate intact (NOT recompute_status, which would flip a
    proxy order back to OPEN / awaiting-estimate).

Intended to run hourly (server tz = Asia/Jakarta).

Usage:
    python manage.py lapse_ignored_carry_offers            # lapse everything past 24h
    python manage.py lapse_ignored_carry_offers --dry-run  # show what would lapse
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.trips import workflow
from apps.trips.constants import OfferStatus, Status
from apps.trips.models import TravelerOffer

OFFER_DECISION_HOURS = 24


class Command(BaseCommand):
    help = "Lapse pending carry offers on proxy orders the buyer hasn't accepted within 24h."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="List what would lapse without changing anything.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        cutoff = timezone.now() - timedelta(hours=OFFER_DECISION_HOURS)

        qs = TravelerOffer.objects.filter(
            offer_status=OfferStatus.PENDING,
            created_at__lt=cutoff,
            order__plan__isnull=True,
            order__cargo_only=False,
            order__proxy_buyer__isnull=False,
            order__status=Status.RESPONDED,  # buyer hasn't accepted the offer
        ).select_related("order")

        if not qs:
            self.stdout.write("No ignored carry offers to lapse.")
            return

        lapsed = 0
        for offer in qs:
            order = offer.order
            label = f"offer #{offer.id} on {order.reference} (offered {offer.created_at:%Y-%m-%d %H:%M})"
            if dry:
                self.stdout.write(f"[dry-run] would lapse {label}")
                continue
            with transaction.atomic():
                offer.offer_status = OfferStatus.WITHDRAWN
                offer.save(update_fields=["offer_status", "updated_at"])
                workflow.release_offer_reservation(offer)
                # Order stays RESPONDED (estimate still valid) → back on the
                # carrier board for another traveler. Deliberately NOT calling
                # recompute_status(), which would flip it to OPEN.
            lapsed += 1
            self.stdout.write(self.style.SUCCESS(f"Lapsed {label}"))

        if not dry:
            self.stdout.write(self.style.SUCCESS(f"Done. Lapsed {lapsed} offer(s)."))
