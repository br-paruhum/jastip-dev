"""Cancel ACCEPTED requests where the buyer has not uploaded any deposit proof
within the configured deadline (default: 24 hours, set via PAYMENT_DEADLINE_HOURS).

A request is skipped if it already has any deposit payment record (even Pending),
so a buyer who uploaded proof just before the cron runs is safe.

Usage:
    python manage.py cancel_overdue_deposits            # cancel overdue requests
    python manage.py cancel_overdue_deposits --dry-run  # preview only
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.trips import workflow
from apps.trips.constants import Status
from apps.trips.models import Order, Payment


class Command(BaseCommand):
    help = "Cancel ACCEPTED requests where no deposit was paid within the deadline."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="List what would be cancelled without changing anything.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        deadline_hours = getattr(settings, "PAYMENT_DEADLINE_HOURS", 24)
        cutoff = timezone.now() - timedelta(hours=deadline_hours)

        has_deposit = Payment.objects.filter(
            transaction__request=OuterRef("pk"),
            kind=Payment.Kind.DEPOSIT,
        )

        overdue = Order.objects.filter(
            status=Status.ACCEPTED,
            updated_at__lt=cutoff,
        ).exclude(
            Exists(has_deposit)
        ).select_related("plan__traveler", "buyer")

        if not overdue:
            self.stdout.write("No overdue requests to cancel.")
            return

        cancelled = 0
        for req in overdue:
            label = f"{req.reference} (accepted at {req.updated_at:%Y-%m-%d %H:%M} UTC, buyer: {req.buyer.email})"
            if dry:
                self.stdout.write(f"[dry-run] would cancel {label}")
                continue
            workflow.on_deposit_cancelled(req)
            cancelled += 1
            self.stdout.write(self.style.SUCCESS(f"Cancelled {label}"))

        if not dry:
            self.stdout.write(self.style.SUCCESS(f"Done. Cancelled {cancelled} request(s)."))
