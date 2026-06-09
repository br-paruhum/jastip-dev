from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.trips import workflow
from apps.trips.constants import Currency, Status
from apps.trips.models import BuyRequest, Payment, RequestItem, TravelPlan, Transaction

User = get_user_model()


def make_user(email):
    return User.objects.create_user(
        email=email, password="pw", full_name=email.split("@")[0],
        phone_country_code="+62", phone_number="81200000000", phone_verified=True,
    )


class LifecycleTests(TestCase):
    def setUp(self):
        self.traveler = make_user("traveler@x.com")
        self.buyer = make_user("buyer@x.com")
        self.plan = TravelPlan.objects.create(
            traveler=self.traveler, travel_date=date.today() + timedelta(days=10),
            from_city="Tokyo", from_country="Japan", to_city="Jakarta", to_country="Indonesia",
            available_weight_kg=Decimal("5"), shipment_currency=Currency.USD,
            shipment_cost_per_kg=Decimal("10"), margin_percent=Decimal("10"),
        )
        self.req = BuyRequest.objects.create(plan=self.plan, buyer=self.buyer)
        RequestItem.objects.create(request=self.req, name="Camera", quantity=1, position=1)
        RequestItem.objects.create(request=self.req, name="Lens", quantity=2, position=2)
        self.tx = Transaction.objects.create(request=self.req)

    def test_reference_generated(self):
        self.assertTrue(self.plan.reference)
        self.assertTrue(self.req.reference.startswith("REQ-"))

    def test_money_math(self):
        items = list(self.req.items.all())
        items[0].estimated_unit_cost = Decimal("100")
        items[0].save()
        items[1].estimated_unit_cost = Decimal("50")  # x2 = 100
        items[1].save()
        # estimated items = 100 + 100 = 200
        self.assertEqual(self.req.items_estimated_total, Decimal("200.00"))
        # shipment = 5kg * 10 = 50
        self.assertEqual(self.req.shipment_cost, Decimal("50.00"))
        # deposit = 50% items + shipment = 100 + 50 = 150
        self.assertEqual(self.req.deposit_due, Decimal("150.00"))
        # commission 2.5% of 150 = 3.75; payout = 146.25
        self.assertEqual(self.tx.commission_amount, Decimal("3.75"))
        self.assertEqual(self.tx.payout_to_traveler, Decimal("146.25"))

    def test_full_lifecycle(self):
        for item in self.req.items.all():
            item.estimated_unit_cost = Decimal("100")
            item.actual_unit_cost = Decimal("100")
            item.save()

        workflow.on_request_submitted(self.req)
        self.assertEqual(self.req.status, Status.REQUEST_RECEIVED)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, Status.REQUEST_RECEIVED)

        workflow.on_request_accepted(self.req)
        self.assertEqual(self.req.status, Status.ACCEPTED)

        # Buyer pays deposit, admin verifies
        dep = Payment.objects.create(
            transaction=self.tx, direction=Payment.Direction.INBOUND,
            kind=Payment.Kind.DEPOSIT, currency=Currency.USD, amount=self.req.deposit_due,
        )
        dep.mark_verified()
        workflow.on_deposit_verified(self.req)
        self.assertEqual(self.req.status, Status.DEPOSIT_PAID)

        workflow.on_items_purchased(self.req)
        self.assertEqual(self.req.status, Status.ITEMS_PURCHASED)

        self.req.custom_fare_amount = Decimal("20")
        self.req.save()
        workflow.on_package_arrived(self.req)
        self.assertEqual(self.req.status, Status.PACKAGE_ARRIVED)

        # actual items 300 (100 + 200) + margin 10% of 300 = 30 + shipment 50 + custom 20 = 400
        self.assertEqual(self.req.invoice_total, Decimal("400.00"))
        # deposit = 50% of est items (300) + shipment (50) = 200 -> unpaid = 200
        self.assertEqual(self.req.amount_paid, Decimal("200.00"))
        self.assertEqual(self.req.unpaid_amount, Decimal("200.00"))

        bal = Payment.objects.create(
            transaction=self.tx, direction=Payment.Direction.INBOUND,
            kind=Payment.Kind.BALANCE, currency=Currency.USD, amount=self.req.unpaid_amount,
        )
        bal.mark_verified()
        workflow.on_balance_verified(self.req)
        self.assertEqual(self.req.status, Status.READY_FOR_PICKUP)
        self.assertEqual(self.req.unpaid_amount, Decimal("0.00"))

        workflow.on_cleared(self.req)
        self.assertEqual(self.req.status, Status.CLOSED)

    def test_reject_reopens_plan(self):
        workflow.on_request_submitted(self.req)
        workflow.on_request_rejected(self.req, reason="Out of stock")
        self.assertEqual(self.req.status, Status.REJECTED)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, Status.REOPEN)
        self.assertTrue(self.plan.is_open)


class ProfileGateTests(TestCase):
    def test_profile_complete_requires_name_and_verified_phone(self):
        u = User.objects.create_user(email="x@y.com", password="pw")
        self.assertFalse(u.profile_complete)
        u.full_name = "Jane"
        self.assertFalse(u.profile_complete)
        u.phone_verified = True
        self.assertTrue(u.profile_complete)
