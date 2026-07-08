"""Carrier-First matching pass (see PLAN-carrier-first-orders.md).

Two jobs, run together on a short cadence (e.g. every 10 minutes):
  1. expire — pending CarrierMatch rows past their accept window → expired
     (releases the held weight so the board reads honestly again);
  2. match  — surface any newly-qualifying queued carriers onto RESPONDED
     ('Looking for a Carrier') Products orders, notifying the buyer.

Expiry runs first so freed capacity is available to the same pass.

Usage:
    python manage.py match_carriers            # expire stale holds, then surface new matches
    python manage.py match_carriers --dry-run  # report only; make no changes, send nothing
    python manage.py match_carriers --quiet     # surface matches but suppress buyer notifications
"""

from django.core.management.base import BaseCommand

from apps.trips import matching
from apps.trips.constants import MatchStatus
from apps.trips.models import CarrierMatch


class Command(BaseCommand):
    help = "Carrier-First: expire stale holds and surface matching queued carriers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without writing or notifying.",
        )
        parser.add_argument(
            "--quiet", action="store_true",
            help="Surface matches but suppress buyer notifications.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            from django.utils import timezone
            stale = CarrierMatch.objects.filter(
                status=MatchStatus.PENDING, window_expires_at__lt=timezone.now()
            ).count()
            self.stdout.write(f"[dry-run] would expire {stale} stale pending match(es)")
            self.stdout.write("[dry-run] skipping match pass (would create/notify)")
            return

        # 2-flow model: carriers are bound at order time (Flow-2) or found via the
        # TravelerOffer board (Flow-1), so there is no auto-surface pass anymore —
        # this command only expires timed-out Flow-2 estimate holds, freeing weight.
        expired = matching.expire_stale_matches()
        self.stdout.write(self.style.SUCCESS(
            f"Carrier-First: expired {expired} stale hold(s)."
        ))
