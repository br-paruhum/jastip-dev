"""Cancel buyer-first legs that missed their drop-off deadline (travel_date − 1
day) once the grace period (the order's max_acceptable_date) has also expired
with no manual deposit reassignment by admin. See PLAN-buyer-first-orders.md §6.

Refunds whatever deposit was verified-paid, minus a 2.5% platform fee, as a
PENDING outbound LegPayment — admin verifies it once the money has actually
been wired back to the buyer, same as any other refund (LegPaymentAdmin
"Selected Payment(s) Verified" action).

Intended to run daily at 01:00 (server tz = Asia/Jakarta), alongside close_cleared.

Usage:
    python manage.py expire_missed_dropoffs            # cancel everything past the grace period now
    python manage.py expire_missed_dropoffs --dry-run  # show what would be cancelled
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.trips.constants import LegStatus, OfferStatus
from apps.trips.models import LegPayment, LegTransaction, TravelerOffer

REFUND_FEE_RATE = Decimal("0.025")
TWO_PLACES = Decimal("0.01")


class Command(BaseCommand):
    help = "Cancel buyer-first legs past their drop-off grace period; refund the deposit minus a 2.5% fee."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="List what would be cancelled without changing anything.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        today = timezone.localtime().date()

        qs = TravelerOffer.objects.filter(
            offer_status=OfferStatus.SELECTED, leg_status__isnull=True,
        ).select_related("order", "traveler")

        cancelled = 0
        for leg in qs:
            if leg.drop_off_deadline >= today:
                continue  # drop-off deadline not yet passed
            if leg.order.max_acceptable_date >= today:
                continue  # still in the grace period
            label = f"{leg.order.reference} leg by {leg.traveler} (deadline {leg.drop_off_deadline:%Y-%m-%d})"
            if dry:
                self.stdout.write(f"[dry-run] would cancel {label}")
                cancelled += 1
                continue

            refund_amount = (leg.deposit_paid_amount * (Decimal("1") - REFUND_FEE_RATE)).quantize(TWO_PLACES)
            if refund_amount > 0:
                tx, _ = LegTransaction.objects.get_or_create(leg=leg)
                LegPayment.objects.create(
                    transaction=tx,
                    direction=LegPayment.Direction.OUTBOUND,
                    kind=LegPayment.Kind.REFUND,
                    currency=leg.order.currency,
                    amount=refund_amount,
                    note="Auto-refund: missed drop-off deadline, minus 2.5% platform fee.",
                )
            leg.leg_status = LegStatus.DROPOFF_MISSED
            leg.save(update_fields=["leg_status", "updated_at"])
            leg.order.recompute_status()
            cancelled += 1
            self.stdout.write(self.style.SUCCESS(f"Cancelled {label} — refund {refund_amount}"))

        if cancelled == 0:
            self.stdout.write("No legs to cancel.")
        elif not dry:
            self.stdout.write(self.style.SUCCESS(f"Done. Cancelled {cancelled} leg(s)."))
