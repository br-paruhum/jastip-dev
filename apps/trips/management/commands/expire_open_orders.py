"""Flip buyer-first orders past their deadline with zero offers ever received to
NO_RESPONSE. See PLAN-buyer-first-orders.md §6.

Intended to run daily at 01:00 (server tz = Asia/Jakarta), alongside close_cleared.

Usage:
    python manage.py expire_open_orders            # flip everything past the deadline now
    python manage.py expire_open_orders --dry-run  # show what would expire
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.trips.constants import Status
from apps.trips.models import Order


class Command(BaseCommand):
    help = "Flip buyer-first orders past max_acceptable_date with zero offers ever received to NO_RESPONSE."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="List what would expire without changing anything.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        today = timezone.localtime().date()

        qs = Order.objects.filter(
            plan__isnull=True,
            status=Status.OPEN,
            max_acceptable_date__lt=today,
            traveler_offers__isnull=True,
        ).select_related("buyer")

        if not qs:
            self.stdout.write("No open orders to expire.")
            return

        expired = 0
        for order in qs:
            label = f"{order.reference} (deadline {order.max_acceptable_date:%Y-%m-%d})"
            if dry:
                self.stdout.write(f"[dry-run] would expire {label}")
                continue
            order.status = Status.NO_RESPONSE
            order.save(update_fields=["status", "updated_at"])
            expired += 1
            self.stdout.write(self.style.SUCCESS(f"Expired {label} — NO_RESPONSE"))

        if not dry:
            self.stdout.write(self.style.SUCCESS(f"Done. Expired {expired} order(s)."))
