"""Simulate the home-page listing time-window for a plan or order.

The Closed/Locked + hidden rules are display-only and read the posting's anchor
moment (a plan's travel_date+travel_time, or an order's max_acceptable_date)
against "now". The cleanest way to test them is to move the data, not the clock:
this command shifts that anchor to one of three states and prints the resulting
flags so you can refresh the home page and verify.

Usage:
    python manage.py sim_listing plan  <id> open    # ~2 days out  -> Open / joinable
    python manage.py sim_listing plan  <id> lock     # ~12h out     -> Closed / Locked (still listed)
    python manage.py sim_listing plan  <id> hide     # ~2 days past -> hidden from home
    python manage.py sim_listing order <id> lock     # same, for a buyer-first order
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.trips.models import BuyRequest, TravelPlan

OFFSETS = {           # state -> hours from now for the anchor moment
    "open": +48,
    "lock": +12,      # inside the 24h pre-departure lock window
    "hide": -48,      # more than 24h past the anchor
}


class Command(BaseCommand):
    help = "Shift a plan/order's listing anchor to test the home Closed/Locked/hidden rules."

    def add_arguments(self, parser):
        parser.add_argument("kind", choices=["plan", "order"])
        parser.add_argument("id", type=int)
        parser.add_argument("state", choices=list(OFFSETS))

    def handle(self, *args, **opts):
        target = timezone.localtime(timezone.now() + timedelta(hours=OFFSETS[opts["state"]]))

        if opts["kind"] == "plan":
            obj = TravelPlan.objects.filter(pk=opts["id"]).first()
            if not obj:
                raise CommandError(f"TravelPlan {opts['id']} not found")
            obj.travel_date = target.date()
            obj.travel_time = target.time().replace(microsecond=0)
            obj.save(update_fields=["travel_date", "travel_time", "updated_at"])
            anchor = f"{obj.travel_date} {obj.travel_time}"
        else:
            obj = BuyRequest.objects.filter(pk=opts["id"], plan__isnull=True).first()
            if not obj:
                raise CommandError(f"Buyer-first BuyRequest {opts['id']} not found")
            obj.max_acceptable_date = target.date()
            obj.save(update_fields=["max_acceptable_date", "updated_at"])
            anchor = f"{obj.max_acceptable_date} (deadline, midnight)"

        self.stdout.write(self.style.SUCCESS(
            f"{opts['kind']} {obj.reference}: anchor -> {anchor}  |  "
            f"listing_locked={obj.listing_locked}  listing_expired={obj.listing_expired}"
        ))
