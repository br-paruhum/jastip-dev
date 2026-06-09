"""Close transactions the buyer marked Clear once the grace period elapses.

Intended to run daily from cron. Finds BuyRequests in the CLEAR status whose
`cleared_at` is older than the grace window (default 24h = "next day") and
closes them, which triggers the traveler payout.

Usage:
    python manage.py close_cleared                # close those cleared >= 24h ago
    python manage.py close_cleared --grace-hours 0  # close all CLEAR now (testing)
    python manage.py close_cleared --dry-run        # show what would close
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.trips import workflow
from apps.trips.constants import Status
from apps.trips.models import BuyRequest


class Command(BaseCommand):
    help = "Close CLEAR transactions past the grace period (triggers traveler payout)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--grace-hours", type=int, default=24,
            help="Hours a request must stay CLEAR before auto-closing (default 24).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="List what would be closed without changing anything.",
        )

    def handle(self, *args, **options):
        grace = options["grace_hours"]
        dry = options["dry_run"]
        cutoff = timezone.now() - timedelta(hours=grace)

        qs = BuyRequest.objects.filter(
            status=Status.CLEAR, cleared_at__isnull=False, cleared_at__lte=cutoff
        ).select_related("plan__traveler", "buyer")

        if not qs:
            self.stdout.write(f"No CLEAR transactions older than {grace}h to close.")
            return

        closed = 0
        for req in qs:
            label = f"{req.reference} (clear since {req.cleared_at:%Y-%m-%d %H:%M})"
            if dry:
                self.stdout.write(f"[dry-run] would close {label}")
                continue
            workflow.on_cleared(req)
            closed += 1
            self.stdout.write(self.style.SUCCESS(f"Closed {label} — payout {req.transaction.payout_to_traveler}"))

        if not dry:
            self.stdout.write(self.style.SUCCESS(f"Done. Closed {closed} transaction(s)."))
