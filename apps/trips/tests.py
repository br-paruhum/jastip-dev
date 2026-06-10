from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

# Tests that render full pages must not require a collected staticfiles manifest.
_NO_MANIFEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

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
        # Traveler's estimated weight for this package drives the shipment cost
        # (5 kg × 10 = 50).
        self.req = BuyRequest.objects.create(
            plan=self.plan, buyer=self.buyer, estimated_weight_kg=Decimal("5")
        )
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
        # commission + payout are 2.5% of the full invoice, settled at close —
        # asserted with concrete amounts in the lifecycle test.

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

        # Payout: traveler is paid the FULL invoice less the 2.5% fee, at CLOSE.
        # commission = 2.5% of invoice 400 = 10.00; payout = 400 - 10 = 390.00
        self.assertEqual(self.tx.commission_amount, Decimal("10.00"))
        self.assertEqual(self.tx.payout_to_traveler, Decimal("390.00"))

        bal = Payment.objects.create(
            transaction=self.tx, direction=Payment.Direction.INBOUND,
            kind=Payment.Kind.BALANCE, currency=Currency.USD, amount=self.req.unpaid_amount,
        )
        bal.mark_verified()
        workflow.on_balance_verified(self.req)
        self.assertEqual(self.req.status, Status.READY_FOR_PICKUP)
        self.assertEqual(self.req.unpaid_amount, Decimal("0.00"))

        # Buyer marks Clear -> CLEAR (not closed yet); cron closes it later.
        workflow.on_buyer_cleared(self.req)
        self.assertEqual(self.req.status, Status.CLEAR)
        self.assertIsNotNone(self.req.cleared_at)

        workflow.on_cleared(self.req)
        self.assertEqual(self.req.status, Status.CLOSED)

    def test_invoice_pdf_generated(self):
        from apps.trips.invoices import render_invoice_pdf
        for item in self.req.items.all():
            item.estimated_unit_cost = Decimal("1500")
            item.save()
        pdf = render_invoice_pdf(self.req)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1000)

    def test_accept_attaches_invoice(self):
        from django.core import mail
        for item in self.req.items.all():
            item.estimated_unit_cost = Decimal("100")
            item.save()
        workflow.on_request_submitted(self.req)
        workflow.on_request_accepted(self.req)
        accept_mail = [m for m in mail.outbox if "accepted" in m.subject.lower()]
        self.assertTrue(accept_mail)
        self.assertTrue(accept_mail[0].attachments)
        fname, content, mimetype = accept_mail[0].attachments[0]
        self.assertTrue(fname.endswith(".pdf"))
        self.assertEqual(mimetype, "application/pdf")

    def test_actual_weight_reconciliation(self):
        # Bring it to the point where the estimated balance is fully paid.
        for item in self.req.items.all():
            item.estimated_unit_cost = Decimal("100")
            item.actual_unit_cost = Decimal("100")
            item.save()
        # estimated weight 5kg -> shipment 50; invoice (actual items 300 + margin 30
        # + shipment 50) = 380. Pay it all so unpaid == 0 at estimated weight.
        self.assertEqual(self.req.shipment_cost, Decimal("50.00"))
        Payment.objects.create(
            transaction=self.tx, direction=Payment.Direction.INBOUND,
            kind=Payment.Kind.BALANCE, currency=Currency.USD,
            amount=self.req.invoice_total, status=Payment.PaymentStatus.VERIFIED,
        )
        self.assertEqual(self.req.unpaid_amount, Decimal("0.00"))

        deposit_before = self.req.deposit_due
        self.assertEqual(self.req.estimated_shipment_cost, Decimal("50.00"))

        # Actual weight HIGHER -> extra due, explicit adjustment, deposit unchanged
        self.req.actual_weight_kg = Decimal("8")  # 8*10 = 80 shipment (+30)
        self.req.save()
        self.assertEqual(self.req.shipment_cost, Decimal("80.00"))
        self.assertEqual(self.req.shipment_adjustment, Decimal("30.00"))
        self.assertEqual(self.req.deposit_due, deposit_before)  # deposit stays on estimate
        self.assertEqual(self.req.extra_due, Decimal("30.00"))
        self.assertEqual(self.req.refund_due, Decimal("0.00"))

        # Actual weight LOWER -> refund due
        self.req.actual_weight_kg = Decimal("3")  # 3*10 = 30 shipment (-20)
        self.req.save()
        self.assertEqual(self.req.refund_due, Decimal("20.00"))
        self.assertEqual(self.req.extra_due, Decimal("0.00"))

    def test_reject_reopens_plan(self):
        workflow.on_request_submitted(self.req)
        workflow.on_request_rejected(self.req, reason="Out of stock")
        self.assertEqual(self.req.status, Status.REJECTED)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, Status.REOPEN)
        self.assertTrue(self.plan.is_open)


class CloseCronTests(TestCase):
    def setUp(self):
        traveler = make_user("t2@x.com")
        buyer = make_user("b2@x.com")
        plan = TravelPlan.objects.create(
            traveler=traveler, travel_date=date.today() + timedelta(days=5),
            from_city="Seoul", from_country="South Korea", to_city="Bandung",
            to_country="Indonesia", available_weight_kg=Decimal("3"),
            shipment_currency=Currency.USD, shipment_cost_per_kg=Decimal("8"),
            margin_percent=Decimal("0"),
        )
        self.req = BuyRequest.objects.create(plan=plan, buyer=buyer, status=Status.READY_FOR_PICKUP)
        RequestItem.objects.create(request=self.req, name="Book", quantity=1,
                                   estimated_unit_cost=Decimal("10"), actual_unit_cost=Decimal("10"))
        Transaction.objects.create(request=self.req)
        workflow.on_buyer_cleared(self.req)

    def test_recent_clear_not_closed_by_default_grace(self):
        # cleared just now -> 24h grace means it stays CLEAR
        call_command("close_cleared")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, Status.CLEAR)

    def test_grace_zero_closes_immediately(self):
        call_command("close_cleared", "--grace-hours", "0")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, Status.CLOSED)

    def test_aged_clear_is_closed(self):
        BuyRequest.objects.filter(pk=self.req.pk).update(
            cleared_at=timezone.now() - timedelta(days=2)
        )
        call_command("close_cleared")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, Status.CLOSED)


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class RefundTests(TestCase):
    def setUp(self):
        from django.urls import reverse
        self.reverse = reverse
        traveler = make_user("rft@x.com")
        self.buyer = make_user("rfb@x.com")
        plan = TravelPlan.objects.create(
            traveler=traveler, travel_date=date.today() + timedelta(days=9),
            from_city="Hanoi", from_country="Vietnam", to_city="Jakarta",
            to_country="Indonesia", available_weight_kg=Decimal("5"),
            shipment_currency=Currency.USD, shipment_cost_per_kg=Decimal("10"),
            margin_percent=Decimal("0"),
        )
        self.req = BuyRequest.objects.create(plan=plan, buyer=self.buyer, status=Status.PACKAGE_ARRIVED)
        RequestItem.objects.create(request=self.req, name="A", quantity=1)
        tx = Transaction.objects.create(request=self.req)
        # Overpay: invoice_total is 0 here; a 100 verified payment => refund_due 100.
        Payment.objects.create(
            transaction=tx, direction=Payment.Direction.INBOUND, kind=Payment.Kind.BALANCE,
            currency=Currency.USD, amount=Decimal("100"), status=Payment.PaymentStatus.VERIFIED,
        )

    def test_refund_payment_nets_overpaid(self):
        self.assertEqual(self.req.refund_due, Decimal("100.00"))
        # Admin refunds 60 (recorded as an outbound refund payment).
        Payment.objects.create(
            transaction=self.req.transaction, direction=Payment.Direction.OUTBOUND,
            kind=Payment.Kind.REFUND, currency=Currency.USD, amount=Decimal("60"),
            status=Payment.PaymentStatus.VERIFIED,
        )
        self.assertEqual(self.req.total_refunded, Decimal("60.00"))
        # Remaining overpaid is net of the refund already paid.
        self.assertEqual(self.req.refund_due, Decimal("40.00"))
        self.assertEqual(self.req.amount_paid, Decimal("40.00"))

    def test_buyer_submits_refund_details(self):
        self.assertEqual(self.req.refund_due, Decimal("100.00"))
        self.client.force_login(self.buyer)
        resp = self.client.post(self.reverse("trips:request_refund_bank", args=[self.req.pk]), {
            "refund_bank_name": "OCBC", "refund_account_no": "123456",
            "refund_account_name": "Jane Buyer",
        })
        self.assertEqual(resp.status_code, 302)
        self.req.refresh_from_db()
        self.assertTrue(self.req.refund_details_provided)
        self.assertEqual(self.req.refund_account_name, "Jane Buyer")


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class ChatTests(TestCase):
    def setUp(self):
        from django.urls import reverse
        self.reverse = reverse
        self.traveler = make_user("ct@x.com")
        self.buyer = make_user("cb@x.com")
        plan = TravelPlan.objects.create(
            traveler=self.traveler, travel_date=date.today() + timedelta(days=8),
            from_city="Bangkok", from_country="Thailand", to_city="Jakarta",
            to_country="Indonesia", available_weight_kg=Decimal("5"),
            shipment_currency=Currency.THB, shipment_cost_per_kg=Decimal("100"),
            margin_percent=Decimal("5"),
        )
        self.req = BuyRequest.objects.create(plan=plan, buyer=self.buyer)
        RequestItem.objects.create(request=self.req, name="Snacks", quantity=1)

    def test_buyer_message_notifies_traveler(self):
        from django.core import mail
        from apps.trips.models import Message
        self.client.force_login(self.buyer)
        resp = self.client.post(
            self.reverse("trips:request_message", args=[self.req.pk]),
            {"body": "Is the item available?"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Message.objects.filter(request=self.req).count(), 1)
        # traveler is notified by email
        notified = [m for m in mail.outbox if self.traveler.email in m.to]
        self.assertTrue(notified)

    def test_detail_page_renders_chat(self):
        self.client.force_login(self.buyer)
        resp = self.client.get(self.reverse("trips:request_detail", args=[self.req.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Messages")

    def test_outsider_cannot_post(self):
        stranger = make_user("stranger@x.com")
        self.client.force_login(stranger)
        resp = self.client.post(
            self.reverse("trips:request_message", args=[self.req.pk]),
            {"body": "hi"},
        )
        from apps.trips.models import Message
        self.assertEqual(Message.objects.filter(request=self.req).count(), 0)


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class ReviewDraftTests(TestCase):
    def setUp(self):
        from django.urls import reverse
        self.reverse = reverse
        traveler = make_user("rt@x.com")
        buyer = make_user("rb@x.com")
        plan = TravelPlan.objects.create(
            traveler=traveler, travel_date=date.today() + timedelta(days=6),
            from_city="Osaka", from_country="Japan", to_city="Medan",
            to_country="Indonesia", available_weight_kg=Decimal("10"),
            shipment_currency=Currency.JPY, shipment_cost_per_kg=Decimal("1000"),
            margin_percent=Decimal("10"),
        )
        self.req = BuyRequest.objects.create(plan=plan, buyer=buyer, status=Status.REQUEST_RECEIVED)
        self.item = RequestItem.objects.create(request=self.req, name="Toy", quantity=1)
        self.traveler = traveler

    def _post(self, decision, weight=""):
        self.client.force_login(self.traveler)
        return self.client.post(self.reverse("trips:request_review", args=[self.req.pk]), {
            "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "1",
            "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000",
            "items-0-id": str(self.item.pk), "items-0-estimated_unit_cost": "1200",
            "estimated_weight_kg": weight, "decision": decision,
        })

    def test_save_draft_keeps_review_status(self):
        self._post("draft", weight="3.5")
        self.req.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.req.status, Status.REQUEST_RECEIVED)  # still reviewable
        self.assertEqual(self.req.estimated_weight_kg, Decimal("3.5"))
        self.assertEqual(self.item.estimated_unit_cost, Decimal("1200"))

    def test_accept_requires_weight(self):
        self._post("accept", weight="")  # no weight
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, Status.REQUEST_RECEIVED)  # blocked
        self._post("accept", weight="4")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, Status.ACCEPTED)


class ProfileGateTests(TestCase):
    def test_profile_complete_requires_name_and_verified_phone(self):
        u = User.objects.create_user(email="x@y.com", password="pw")
        self.assertFalse(u.profile_complete)
        u.full_name = "Jane"
        self.assertFalse(u.profile_complete)
        u.phone_verified = True
        self.assertTrue(u.profile_complete)
