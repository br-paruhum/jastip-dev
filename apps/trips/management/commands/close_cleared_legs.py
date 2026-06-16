"""Close buyer-first legs the buyer marked Clear, the day after they clear it.

Per-leg equivalent of close_cleared.py — see that command's docstring for the
day-cutoff rationale. Intended to run daily at 01:00 (server tz = Asia/Jakarta),
alongside close_cleared.

Usage:
    python manage.py close_cleared_legs            # close everything cleared before today (WIB)
    python manage.py close_cleared_legs --all      # close every CLEAR leg now (manual / testing)
    python manage.py close_cleared_legs --dry-run  # show what would close
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.trips.constants import LegStatus
from apps.trips.models import TravelerOffer


class Command(BaseCommand):
    help = "Close CLEAR buyer-first legs cleared before today (triggers the traveler payout)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all", action="store_true",
            help="Close every CLEAR leg now, ignoring the day cutoff (manual/testing).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="List what would be closed without changing anything.",
        )

    def handle(self, *args, **options):
        close_all = options["all"]
        dry = options["dry_run"]

        qs = TravelerOffer.objects.filter(
            leg_status=LegStatus.CLEAR, cleared_at__isnull=False
        ).select_related("order", "traveler")

        if not close_all:
            today_start = timezone.localtime().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            qs = qs.filter(cleared_at__lt=today_start)

        if not qs:
            self.stdout.write("No CLEAR legs to close.")
            return

        closed = 0
        for leg in qs:
            label = f"{leg.order.reference} leg by {leg.traveler} (clear since {leg.cleared_at:%Y-%m-%d %H:%M})"
            if dry:
                self.stdout.write(f"[dry-run] would close {label}")
                continue
            leg.leg_status = LegStatus.CLOSED
            leg.save(update_fields=["leg_status", "updated_at"])
            leg.order.recompute_status()
            closed += 1
            payout = leg.transaction.payout_to_traveler if hasattr(leg, "transaction") else "n/a"
            self.stdout.write(self.style.SUCCESS(f"Closed {label} — payout {payout}"))

        if not dry:
            self.stdout.write(self.style.SUCCESS(f"Done. Closed {closed} leg(s)."))
