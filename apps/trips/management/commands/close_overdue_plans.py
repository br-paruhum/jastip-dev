"""Close carrier-only travel plans PLAN_BOARD_RETENTION_DAYS (2) days after the
travel date. Closed plans drop off the home "Looking for Spare Baggage Buyer"
board and are considered done. The plans' own buy-request legs keep their
independent lifecycle.

Intended to run daily at 01:00 (server tz = Asia/Jakarta), alongside
close_cleared / expire_open_orders.

Usage:
    python manage.py close_overdue_plans            # close everything past the window
    python manage.py close_overdue_plans --dry-run  # show what would close
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.trips.constants import Status
from apps.trips.models import PLAN_BOARD_RETENTION_DAYS, TravelPlan


class Command(BaseCommand):
    help = "Close carrier-only travel plans 2 days after their travel date."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="List what would close without changing anything.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        today = timezone.localtime().date()
        cutoff = today - timedelta(days=PLAN_BOARD_RETENTION_DAYS)  # travel_date <= cutoff

        qs = (
            TravelPlan.objects.filter(carrier_only=True, travel_date__lte=cutoff)
            .exclude(status__in=[Status.CLOSED, Status.CANCELLED])
            .select_related("traveler")
        )

        if not qs:
            self.stdout.write("No overdue travel plans to close.")
            return

        closed = 0
        for plan in qs:
            label = f"{plan.reference} (travel {plan.travel_date:%Y-%m-%d})"
            if dry:
                self.stdout.write(f"[dry-run] would close {label}")
                continue
            plan.status = Status.CLOSED
            plan.save(update_fields=["status", "updated_at"])
            closed += 1
            self.stdout.write(self.style.SUCCESS(f"Closed {label}"))

        if not dry:
            self.stdout.write(self.style.SUCCESS(f"Done. Closed {closed} plan(s)."))
