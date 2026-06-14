"""Auto-transition ITEMS_PURCHASED requests to PACKAGE_ARRIVED.

Runs daily at 01:00 (server tz = Asia/Jakarta). If the plan's travel date
was 2+ days ago and the traveler has not yet manually marked the package as
arrived, the system does it automatically.

Usage:
    python manage.py auto_package_arrived            # transition overdue requests
    python manage.py auto_package_arrived --dry-run  # preview only
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.trips import workflow
from apps.trips.constants import Status
from apps.trips.models import BuyRequest


class Command(BaseCommand):
    help = "Auto-arrive ITEMS_PURCHASED requests whose travel date passed 2+ days ago."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Preview without making any changes.",
        )
        parser.add_argument(
            "--travel-date", metavar="YYYY-MM-DD", default=None,
            help="Pretend today is this date (for testing). E.g. --travel-date 2026-06-10",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        if options["travel_date"]:
            from datetime import date
            today = date.fromisoformat(options["travel_date"])
            self.stdout.write(f"[simulating today = {today}]")
        else:
            today = timezone.localtime().date()
        # travel_date + 2 <= today  →  travel_date <= today - 2
        cutoff = today - timedelta(days=2)

        overdue = BuyRequest.objects.filter(
            status=Status.ITEMS_PURCHASED,
            plan__travel_date__lte=cutoff,
        ).select_related("plan__traveler", "buyer")

        if not overdue.exists():
            self.stdout.write("No requests to auto-arrive.")
            return

        arrived = 0
        for req in overdue:
            label = (
                f"{req.reference} "
                f"(travel {req.plan.travel_date}, buyer: {req.buyer.email})"
            )
            if dry:
                self.stdout.write(f"[dry-run] would auto-arrive {label}")
                continue
            workflow.on_package_arrived(req)
            arrived += 1
            self.stdout.write(self.style.SUCCESS(f"Auto-arrived {label}"))

        if not dry:
            self.stdout.write(self.style.SUCCESS(f"Done. Auto-arrived {arrived} request(s)."))
