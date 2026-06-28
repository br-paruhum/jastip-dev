"""Close transactions the buyer marked Clear, the day after they clear it.

Intended to run daily at 01:00 (server tz = Asia/Jakarta). Finds Orders in
the CLEAR status that were cleared on an earlier calendar day and closes them,
which triggers the traveler payout. So a buyer who clears at any time today has
until the end of today; the next 01:00 run closes it tomorrow morning.

Usage:
    python manage.py close_cleared            # close everything cleared before today (WIB)
    python manage.py close_cleared --all      # close every CLEAR now (manual / testing)
    python manage.py close_cleared --dry-run  # show what would close
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.trips import workflow
from apps.trips.constants import Status
from apps.trips.models import Order


class Command(BaseCommand):
    help = "Close CLEAR transactions cleared before today (triggers the traveler payout)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all", action="store_true",
            help="Close every CLEAR transaction now, ignoring the day cutoff (manual/testing).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="List what would be closed without changing anything.",
        )

    def handle(self, *args, **options):
        close_all = options["all"]
        dry = options["dry_run"]

        qs = Order.objects.filter(
            status=Status.CLEAR, cleared_at__isnull=False
        ).select_related("plan__traveler", "buyer")

        if not close_all:
            # Start of today in the server's local timezone (WIB). Anything
            # cleared before this — i.e. on an earlier calendar day — is closed.
            today_start = timezone.localtime().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            qs = qs.filter(cleared_at__lt=today_start)

        if not qs:
            self.stdout.write("No CLEAR transactions to close.")
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
