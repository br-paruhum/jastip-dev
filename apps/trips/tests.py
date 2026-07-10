import tempfile
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
from apps.trips.constants import Currency, OfferStatus, Status
from apps.trips.models import Order, Payment, RequestItem, TravelPlan, Transaction

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
        self.req = Order.objects.create(
            plan=self.plan, buyer=self.buyer, estimated_weight_kg=Decimal("5")
        )
        RequestItem.objects.create(request=self.req, name="Camera", quantity=1, position=1)
        RequestItem.objects.create(request=self.req, name="Lens", quantity=2, position=2)
        self.tx = Transaction.objects.create(request=self.req)

    def test_reference_generated(self):
        self.assertTrue(self.plan.reference)
        self.assertTrue(self.req.reference.startswith("ORD-"))

    def test_money_math(self):
        items = list(self.req.items.all())
        items[0].estimated_unit_cost = Decimal("100")
        items[0].save()
        items[1].estimated_unit_cost = Decimal("50")  # x2 = 100
        items[1].save()
        # estimated items = 100 + 100 = 200
        self.assertEqual(self.req.items_estimated_total, Decimal("200.00"))
        # estimated shipment = 5kg * 10 = 50
        self.assertEqual(self.req.estimated_shipment_cost, Decimal("50.00"))
        # margin 10% of 200 = 20
        self.assertEqual(self.req.estimated_margin, Decimal("20.00"))
        # deposit = items 200 + margin 20 + shipment 50 = 270 (Task 2: 100% upfront)
        self.assertEqual(self.req.deposit_due, Decimal("270.00"))
        # commission + payout are 2.5% of the full invoice, settled at close —
        # asserted with concrete amounts in the lifecycle test.

    def test_full_lifecycle(self):
        for item in self.req.items.all():
            item.estimated_unit_cost = Decimal("100")
            item.actual_unit_cost = Decimal("100")
            item.actual_quantity = item.quantity
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

        # Traveler records the actual measured weight at arrival.
        self.req.actual_weight_kg = Decimal("5")
        self.req.custom_fare_amount = Decimal("20")
        self.req.save()
        workflow.on_package_arrived(self.req)
        self.assertEqual(self.req.status, Status.PACKAGE_ARRIVED)

        # actual items 300 (100 + 200) + margin 10% of 300 = 30 + shipment 50 + custom 20 = 400
        self.assertEqual(self.req.invoice_total, Decimal("400.00"))
        # deposit = est items 300 + margin 30 + shipment 50 = 380 (Task 2: 100% upfront)
        self.assertEqual(self.req.amount_paid, Decimal("380.00"))
        # unpaid = invoice 400 - deposit 380 = 20
        self.assertEqual(self.req.unpaid_amount, Decimal("20.00"))

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

    def test_actual_weight_drives_invoice(self):
        # Traveler's actual (final-measured) weight drives the actual invoice;
        # the deposit always stays on the estimate.
        for item in self.req.items.all():
            item.estimated_unit_cost = Decimal("100")
            item.actual_unit_cost = Decimal("100")
            item.actual_quantity = item.quantity
            item.save()
        # estimated items = 300, margin 10% = 30, est shipment 5kg*10 = 50
        self.assertEqual(self.req.items_estimated_total, Decimal("300.00"))
        self.assertEqual(self.req.estimated_margin, Decimal("30.00"))
        self.assertEqual(self.req.estimated_shipment_cost, Decimal("50.00"))
        # deposit = items 300 + margin 30 + shipment 50 = 380 (Task 2: 100% upfront)
        self.assertEqual(self.req.deposit_due, Decimal("380.00"))
        deposit_before = self.req.deposit_due

        # Actual weight HIGHER -> invoice grows, deposit unchanged.
        self.req.actual_weight_kg = Decimal("8")  # 8*10 = 80 actual shipment
        self.req.save()
        self.assertEqual(self.req.shipment_cost, Decimal("80.00"))
        # actual items 300 + actual margin 30 + actual shipment 80 + custom 0 = 410
        self.assertEqual(self.req.invoice_total, Decimal("410.00"))
        self.assertEqual(self.req.deposit_due, deposit_before)

        # Pay the deposit; unpaid follows the actual invoice.
        Payment.objects.create(
            transaction=self.tx, direction=Payment.Direction.INBOUND,
            kind=Payment.Kind.DEPOSIT, currency=Currency.USD,
            amount=self.req.deposit_due, status=Payment.PaymentStatus.VERIFIED,
        )
        # unpaid = invoice 410 - deposit 380 = 30
        self.assertEqual(self.req.invoice_unpaid_overpaid, Decimal("30.00"))
        # Balance not paid yet: no final settlement made, full 30 still due.
        self.assertEqual(self.req.final_settlement, Decimal("0.00"))
        self.assertEqual(self.req.total_due, Decimal("30.00"))

        # Buyer pays the balance -> final settlement reflects it, due clears.
        Payment.objects.create(
            transaction=self.tx, direction=Payment.Direction.INBOUND,
            kind=Payment.Kind.BALANCE, currency=Currency.USD,
            amount=Decimal("30.00"), status=Payment.PaymentStatus.VERIFIED,
        )
        self.assertEqual(self.req.final_settlement, Decimal("-30.00"))
        self.assertEqual(self.req.total_due, Decimal("0.00"))

    def test_settlement_zero_after_deposit_before_actuals(self):
        """Deposit verified but no actual purchase yet: the statement
        reconciliation lines read 0, not the whole deposit as 'overpaid'."""
        for item in self.req.items.all():
            item.estimated_unit_cost = Decimal("100")
            item.save()
        self.assertGreater(self.req.deposit_due, Decimal("0"))
        Payment.objects.create(
            transaction=self.tx, direction=Payment.Direction.INBOUND,
            kind=Payment.Kind.DEPOSIT, currency=Currency.USD,
            amount=self.req.deposit_due, status=Payment.PaymentStatus.VERIFIED,
        )
        # Nothing purchased yet -> empty Actual column -> nothing to reconcile.
        self.assertEqual(self.req.invoice_total, Decimal("0.00"))
        self.assertEqual(self.req.invoice_unpaid_overpaid, Decimal("0.00"))
        self.assertEqual(self.req.final_settlement, Decimal("0.00"))
        self.assertEqual(self.req.total_due, Decimal("0.00"))

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
        self.req = Order.objects.create(plan=plan, buyer=buyer, status=Status.READY_FOR_PICKUP)
        RequestItem.objects.create(request=self.req, name="Book", quantity=1,
                                   estimated_unit_cost=Decimal("10"), actual_unit_cost=Decimal("10"))
        Transaction.objects.create(request=self.req)
        workflow.on_buyer_cleared(self.req)

    def test_clear_today_not_closed(self):
        # cleared today -> closes at tomorrow's 01:00 run, so it stays CLEAR now
        call_command("close_cleared")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, Status.CLEAR)

    def test_all_closes_immediately(self):
        call_command("close_cleared", "--all")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, Status.CLOSED)

    def test_cleared_yesterday_is_closed(self):
        # Cleared late yesterday (local) -> the next 01:00 run closes it, even
        # though less than 24h has elapsed.
        local_midnight = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        Order.objects.filter(pk=self.req.pk).update(
            cleared_at=local_midnight - timedelta(minutes=1)
        )
        call_command("close_cleared")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, Status.CLOSED)

    def test_aged_clear_is_closed(self):
        Order.objects.filter(pk=self.req.pk).update(
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
        self.req = Order.objects.create(plan=plan, buyer=self.buyer, status=Status.PACKAGE_ARRIVED)
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
        self.req = Order.objects.create(plan=plan, buyer=self.buyer)
        RequestItem.objects.create(request=self.req, name="Snacks", quantity=1)

    def test_buyer_message_notifies_traveler(self):
        from django.core import mail
        from apps.trips.models import Message
        # Message tab only opens once the order is an active transaction
        # (deposit paid onward) — see Order.message_tab_visible_to (Task 11).
        self.req.status = Status.DEPOSIT_PAID
        self.req.save(update_fields=["status"])
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
        self.req.status = Status.DEPOSIT_PAID
        self.req.save(update_fields=["status"])
        self.client.force_login(self.buyer)
        resp = self.client.get(self.reverse("trips:request_detail", args=[self.req.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Messages")

    def test_chat_locked_before_deposit(self):
        # At the estimate stage (default REQUEST_RECEIVED) the tab is locked for
        # everyone and the buyer cannot post (Task 11 / How-to_260625.pdf note 1).
        from apps.trips.models import Message
        self.assertFalse(self.req.message_tab_visible_to(self.buyer))
        self.client.force_login(self.buyer)
        resp = self.client.get(self.reverse("trips:request_detail", args=[self.req.pk]))
        self.assertNotContains(resp, "Messages")
        self.client.post(
            self.reverse("trips:request_message", args=[self.req.pk]),
            {"body": "too early"},
        )
        self.assertEqual(Message.objects.filter(request=self.req).count(), 0)

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
class MessageTabVisibilityTests(TestCase):
    """Task 11 (How-to_260625.pdf): the Message tab rotates through a pair of
    parties as a proxy (3-party) order advances. Buyer↔Proxy → Proxy↔Carrier →
    Carrier↔Buyer, each handoff revoking the party that drops out. Admin always
    sees it; outsiders never do."""

    def setUp(self):
        from apps.trips.models import ProxyBuyer, TravelerOffer
        self.buyer = make_user("mtb@x.com")
        self.proxy_user = make_user("mtp@x.com")
        self.carrier = make_user("mtc@x.com")
        self.outsider = make_user("mto@x.com")
        self.admin = make_user("mta@x.com")
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        proxy = ProxyBuyer.objects.create(
            name="Bangkok Proxy", country="Thailand", email="p@x.com",
            user=self.proxy_user,
        )
        # Buyer-first proxy order (plan=None) with one confirmed carrier leg, so
        # traveler_user resolves to the carrier and all three parties are present.
        self.order = Order.objects.create(buyer=self.buyer, proxy_buyer=proxy)
        TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier,
            ask_cost_per_kg=Decimal("100"), avail_kg=Decimal("5"),
            travel_date=date.today() + timedelta(days=10),
            from_city="Bangkok", from_country="Thailand",
            to_city="Jakarta", to_country="Indonesia",
            offer_status=OfferStatus.SELECTED, allocated_weight_kg=Decimal("5"),
        )

    def _visible(self, status):
        self.order.status = status
        return {
            "buyer": self.order.message_tab_visible_to(self.buyer),
            "proxy": self.order.message_tab_visible_to(self.proxy_user),
            "carrier": self.order.message_tab_visible_to(self.carrier),
        }

    def test_locked_before_estimate(self):
        # Buyer<->Proxy now opens as soon as the proxy sends the estimate
        # (Status.ESTIMATE_SENT), so OPEN (before any estimate) is the locked case.
        self.assertEqual(
            self._visible(Status.OPEN),
            {"buyer": False, "proxy": False, "carrier": False},
        )

    def test_deposit_paid_pairs_buyer_and_proxy(self):
        self.assertEqual(
            self._visible(Status.DEPOSIT_PAID),
            {"buyer": True, "proxy": True, "carrier": False},
        )

    def test_items_purchased_pairs_proxy_and_carrier(self):
        self.assertEqual(
            self._visible(Status.ITEMS_PURCHASED),
            {"buyer": False, "proxy": True, "carrier": True},
        )

    def test_package_received_pairs_carrier_and_buyer(self):
        self.assertEqual(
            self._visible(Status.PACKAGE_RECEIVED),
            {"buyer": True, "proxy": False, "carrier": True},
        )

    def test_carrier_buyer_pair_holds_through_close(self):
        for status in (Status.PACKAGE_ARRIVED, Status.READY_FOR_PICKUP,
                       Status.RESHIPPING, Status.CLEAR, Status.CLOSED):
            self.assertEqual(
                self._visible(status),
                {"buyer": True, "proxy": False, "carrier": True},
                msg=f"unexpected visibility at {status}",
            )

    def test_admin_always_sees_outsider_never(self):
        self.order.status = Status.DEPOSIT_PAID
        self.assertTrue(self.order.message_tab_visible_to(self.admin))
        self.assertFalse(self.order.message_tab_visible_to(self.outsider))


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class ChatBoxesTests(TestCase):
    """Phase 9 (Task-27): the single rotating Message tab is split into one box
    per counterpart pair. Each party only ever sees/posts to the conversations
    they take part in — the third party never sees them."""

    def setUp(self):
        from django.urls import reverse
        from apps.trips.models import Message, ProxyBuyer, TravelerOffer
        self.reverse = reverse
        self.Message = Message
        self.buyer = make_user("cbb@x.com")
        self.proxy_user = make_user("cbp@x.com")
        self.carrier = make_user("cbc@x.com")
        self.admin = make_user("cba@x.com")
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p@x.com", user=self.proxy_user,
        )
        self.order = Order.objects.create(buyer=self.buyer, proxy_buyer=proxy)
        TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier,
            ask_cost_per_kg=Decimal("100"), avail_kg=Decimal("5"),
            travel_date=date.today() + timedelta(days=10),
            from_city="Bangkok", from_country="Thailand",
            to_city="Jakarta", to_country="Indonesia",
            offer_status=OfferStatus.SELECTED, allocated_weight_kg=Decimal("5"),
        )
        self.order.status = Status.ITEMS_PURCHASED
        self.order.save(update_fields=["status"])

    def _audiences(self, user):
        return [b["audience"] for b in self.order.chat_boxes(user)]

    def test_each_party_sees_only_their_pairs(self):
        A = self.Message.Audience
        self.assertEqual(set(self._audiences(self.buyer)), {A.BUYER_PROXY, A.CARRIER_BUYER})
        self.assertEqual(set(self._audiences(self.proxy_user)), {A.BUYER_PROXY, A.PROXY_CARRIER})
        self.assertEqual(set(self._audiences(self.carrier)), {A.PROXY_CARRIER, A.CARRIER_BUYER})

    def test_admin_sees_all_three_pairs(self):
        A = self.Message.Audience
        self.assertEqual(
            set(self._audiences(self.admin)),
            {A.BUYER_PROXY, A.PROXY_CARRIER, A.CARRIER_BUYER},
        )

    def test_locked_before_estimate(self):
        # Buyer<->Proxy now opens as soon as the proxy sends the estimate
        # (Status.ESTIMATE_SENT), so OPEN (before any estimate) is the locked case.
        self.order.status = Status.OPEN
        self.assertEqual(self.order.chat_boxes(self.buyer), [])

    def test_proxy_carrier_message_hidden_from_buyer(self):
        A = self.Message.Audience
        self.Message.objects.create(
            request=self.order, sender=self.proxy_user,
            audience=A.PROXY_CARRIER, body="carrier-only note",
        )
        for box in self.order.chat_boxes(self.buyer):
            self.assertNotIn("carrier-only note", [m.body for m in box["messages"]])
        # the carrier does see it, in the Proxy-Buyer box
        carrier_bodies = [
            m.body for b in self.order.chat_boxes(self.carrier) for m in b["messages"]
        ]
        self.assertIn("carrier-only note", carrier_bodies)

    def test_post_stamps_audience_from_box(self):
        self.client.force_login(self.proxy_user)
        resp = self.client.post(
            self.reverse("trips:request_message", args=[self.order.pk]),
            {"body": "ready for handover", "audience": "proxy_carrier"},
        )
        self.assertEqual(resp.status_code, 302)
        msg = self.Message.objects.get(request=self.order, body="ready for handover")
        self.assertEqual(msg.audience, self.Message.Audience.PROXY_CARRIER)

    def test_cannot_post_to_a_pair_youre_not_in(self):
        # The buyer is not part of the Proxy↔Carrier conversation.
        self.client.force_login(self.buyer)
        self.client.post(
            self.reverse("trips:request_message", args=[self.order.pk]),
            {"body": "should be rejected", "audience": "proxy_carrier"},
        )
        self.assertFalse(
            self.Message.objects.filter(request=self.order, body="should be rejected").exists()
        )


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class ChatUnreadTests(TestCase):
    """Task-27 follow-up: Message boxes stay collapsed on load and only auto-open
    (box['open']) while they hold a message the viewer hasn't seen yet."""

    def setUp(self):
        from django.urls import reverse
        from apps.trips.models import Message, ProxyBuyer, TravelerOffer
        self.reverse = reverse
        self.Message = Message
        self.buyer = make_user("cub@x.com")
        self.proxy_user = make_user("cup@x.com")
        self.carrier = make_user("cuc@x.com")
        proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p@x.com", user=self.proxy_user,
        )
        self.order = Order.objects.create(buyer=self.buyer, proxy_buyer=proxy)
        TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier,
            ask_cost_per_kg=Decimal("100"), avail_kg=Decimal("5"),
            travel_date=date.today() + timedelta(days=10),
            from_city="Bangkok", from_country="Thailand",
            to_city="Jakarta", to_country="Indonesia",
            offer_status=OfferStatus.SELECTED, allocated_weight_kg=Decimal("5"),
        )
        self.order.status = Status.ITEMS_PURCHASED
        self.order.save(update_fields=["status"])

    def _box(self, user, audience):
        return next(b for b in self.order.chat_boxes(user) if b["audience"] == audience)

    def test_empty_box_is_collapsed(self):
        self.assertFalse(self._box(self.buyer, "buyer_proxy")["open"])

    def test_box_opens_on_unread_message_from_other_party(self):
        self.Message.objects.create(
            request=self.order, sender=self.proxy_user,
            audience=self.Message.Audience.BUYER_PROXY, body="hi buyer",
        )
        # buyer hasn't seen it → their Proxy box auto-opens
        self.assertTrue(self._box(self.buyer, "buyer_proxy")["open"])
        # the carrier isn't in this pair, so it never reaches them at all
        self.assertFalse(any(b["audience"] == "buyer_proxy" for b in self.order.chat_boxes(self.carrier)))

    def test_own_message_does_not_open_box(self):
        self.Message.objects.create(
            request=self.order, sender=self.buyer,
            audience=self.Message.Audience.BUYER_PROXY, body="from me",
        )
        self.assertFalse(self._box(self.buyer, "buyer_proxy")["open"])

    def test_box_collapses_after_marking_seen(self):
        self.Message.objects.create(
            request=self.order, sender=self.proxy_user,
            audience=self.Message.Audience.BUYER_PROXY, body="hi buyer",
        )
        self.assertTrue(self._box(self.buyer, "buyer_proxy")["open"])
        self.client.force_login(self.buyer)
        resp = self.client.post(
            self.reverse("trips:chat_seen", args=[self.order.pk]),
            {"audience": "buyer_proxy"},
        )
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(self._box(self.buyer, "buyer_proxy")["open"])

    def test_cannot_mark_seen_a_pair_youre_not_in(self):
        self.client.force_login(self.buyer)
        resp = self.client.post(
            self.reverse("trips:chat_seen", args=[self.order.pk]),
            {"audience": "proxy_carrier"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_posting_marks_conversation_seen(self):
        # proxy leaves an unread message for the buyer; buyer replies → their box
        # should no longer be flagged unread on the next load.
        self.Message.objects.create(
            request=self.order, sender=self.proxy_user,
            audience=self.Message.Audience.BUYER_PROXY, body="any update?",
        )
        self.client.force_login(self.buyer)
        self.client.post(
            self.reverse("trips:request_message", args=[self.order.pk]),
            {"body": "all good", "audience": "buyer_proxy"},
        )
        self.assertFalse(self._box(self.buyer, "buyer_proxy")["open"])


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class CustomsInvoiceTests(TestCase):
    """Task 12: the customs invoice matches NewCustomsInvoiceFormat.pdf — IDR
    values that include the proxy margin, Country of Origin = the order's origin
    country, a Shipping Charges line, and a fixed package count of 1."""

    def setUp(self):
        from django.utils import timezone
        from apps.trips.models import ProxyBuyer, RequestItem, TravelerOffer
        self.buyer = make_user("cib@x.com")
        self.proxy_user = make_user("cip@x.com")
        self.carrier = make_user("cic@x.com")
        self.buyer.buyer_invoice_address = "Jl. Sudirman No. 8"
        self.buyer.buyer_destination_city = "Jakarta"
        self.buyer.save(update_fields=["buyer_invoice_address", "buyer_destination_city"])
        proxy = ProxyBuyer.objects.create(
            name="Tokyo Proxy", country="Japan", city="Tokyo", email="p@x.com",
            user=self.proxy_user,
        )
        # All-IDR order avoids needing an FX rate; margin 10%, weight 3 kg.
        self.order = Order.objects.create(
            buyer=self.buyer, proxy_buyer=proxy, settlement_currency="IDR",
            proxy_margin_percent=Decimal("10"), status=Status.ITEMS_PURCHASED,
            actual_weight_kg=Decimal("3"),
        )
        TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier,
            ask_cost_per_kg=Decimal("500"), avail_kg=Decimal("5"),
            travel_date=date.today() + timedelta(days=10),
            from_city="Tokyo", from_country="Japan",
            to_city="Jakarta", to_country="Indonesia",
            offer_status=OfferStatus.SELECTED, allocated_weight_kg=Decimal("3"),
        )
        RequestItem.objects.create(
            request=self.order, name="Sambal Ibu Rudy", quantity=2,
            actual_quantity=2, actual_unit_cost=Decimal("1000"),
            purchased_at=timezone.now(),
        )

    def test_values_include_margin_and_shipping(self):
        ci = self.order.customs_invoice()
        self.assertEqual(len(ci["rows"]), 1)
        row = ci["rows"][0]
        self.assertEqual(row.origin, "Japan")
        self.assertEqual(row.unit_value, Decimal("1100.00"))    # 1000 + 10% margin
        self.assertEqual(row.total_value, Decimal("2200.00"))   # × qty 2
        self.assertEqual(ci["subtotal"], Decimal("2200.00"))
        self.assertEqual(ci["shipping"], Decimal("1500.00"))    # 3 kg × 500
        self.assertEqual(ci["total"], Decimal("3700.00"))
        self.assertEqual(ci["currency"], "IDR")
        self.assertEqual(ci["num_packages"], 1)
        self.assertIsNotNone(ci["date"])

    def test_pdf_renders(self):
        from apps.trips.invoices import render_customs_invoice_pdf
        pdf = render_customs_invoice_pdf(self.order)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1000)

    def test_html_view_has_new_format(self):
        from django.urls import reverse
        self.client.force_login(self.carrier)
        resp = self.client.get(reverse("trips:request_customs_invoice", args=[self.order.pk]))
        self.assertEqual(resp.status_code, 200)
        for needle in ("Country of Origin", "Sambal Ibu Rudy", "Number of Package",
                       "Shipping Charges", "ProxyBuying.com", "Japan"):
            self.assertContains(resp, needle)


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
        self.req = Order.objects.create(plan=plan, buyer=buyer, status=Status.REQUEST_RECEIVED)
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


class ChatBoxAutoOpenAtEstimateSentTests(TestCase):
    """Tabs Status Part-1, Step 2 (Note 2-1): once the Proxy sends the estimate
    (status=ESTIMATE_SENT), the Buyer<->Proxy chat box should auto-open on the
    other side whenever the counterpart posts — including a reply on top of an
    already-seen message. This is already covered by the generic Phase-9
    ChatRead/chat_boxes unread mechanism; this test locks that in."""

    def setUp(self):
        from apps.trips.models import Message, ProxyBuyer
        self.Message = Message
        self.buyer = make_user("caob@x.com")
        self.proxy_user = make_user("caop@x.com")
        proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p@x.com", user=self.proxy_user,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, proxy_buyer=proxy, status=Status.ESTIMATE_SENT,
            max_acceptable_date=date.today() + timedelta(days=10),
        )

    def _open_map(self, user):
        return {b["audience"]: b["open"] for b in self.order.chat_boxes(user)}

    def test_proxy_box_opens_on_buyer_message(self):
        self.Message.objects.create(
            request=self.order, sender=self.buyer, body="Can you adjust the margin?",
            audience=self.order.message_audience_for_status(),
        )
        self.assertTrue(self._open_map(self.proxy_user)["buyer_proxy"])
        self.assertFalse(self._open_map(self.buyer)["buyer_proxy"])  # own message, not open for self

    def test_buyer_box_reopens_on_proxy_reply(self):
        self.Message.objects.create(
            request=self.order, sender=self.buyer, body="Can you adjust the margin?",
            audience=self.order.message_audience_for_status(),
        )
        self.Message.objects.create(
            request=self.order, sender=self.proxy_user, body="Sure, revising now.",
            audience=self.order.message_audience_for_status(),
        )
        self.assertTrue(self._open_map(self.buyer)["buyer_proxy"])


class ChatBoxAutoOpenAtStep4Tests(TestCase):
    """Tabs Status Part-1, Step 4 (Note 4-1): once a Carrier has a pending offer
    on a RESPONDED order, the Buyer<->Carrier chat box should auto-open on the
    other side whenever the counterpart posts. Real posts always carry the
    audience of the specific box the form was submitted from (see
    trips.views.request_message) - not derived from order status - so this
    test posts with audience=CARRIER_BUYER explicitly, matching real usage."""

    def setUp(self):
        from apps.trips.models import Message, ProxyBuyer, TravelerOffer
        self.Message = Message
        self.buyer = make_user("s4caob@x.com")
        self.proxy_user = make_user("s4caop@x.com")
        self.carrier = make_user("s4caoc@x.com")
        proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p@x.com", user=self.proxy_user,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, proxy_buyer=proxy, status=Status.RESPONDED,
            max_acceptable_date=date.today() + timedelta(days=10),
        )
        TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier, ask_cost_per_kg=Decimal("100"), avail_kg=Decimal("5"),
            travel_date=date.today() + timedelta(days=10),
            from_city="Bangkok", from_country="Thailand", to_city="Jakarta", to_country="Indonesia",
            offer_status=OfferStatus.PENDING,
        )

    def _open_map(self, user):
        return {b["audience"]: b["open"] for b in self.order.chat_boxes(user)}

    def test_carrier_box_opens_on_buyer_message(self):
        self.Message.objects.create(
            request=self.order, sender=self.buyer, body="Confirming route details",
            audience=self.Message.Audience.CARRIER_BUYER,
        )
        self.assertTrue(self._open_map(self.carrier)["carrier_buyer"])

    def test_buyer_box_reopens_on_carrier_reply(self):
        self.Message.objects.create(
            request=self.order, sender=self.buyer, body="Confirming route details",
            audience=self.Message.Audience.CARRIER_BUYER,
        )
        self.Message.objects.create(
            request=self.order, sender=self.carrier, body="Confirmed, all good",
            audience=self.Message.Audience.CARRIER_BUYER,
        )
        self.assertTrue(self._open_map(self.buyer)["carrier_buyer"])


class ChatBoxAutoOpenAtStep5cTests(TestCase):
    """Tabs Status Part-1, Step 5c (Note 5.4): once admin verifies the deposit
    (status=DEPOSIT_PAID), the Buyer<->Proxy chat box should auto-open on the
    Buyer's side when the Proxy posts (already covered by the generic
    Phase-9 chat_boxes/ChatRead mechanism - locked in here)."""

    def setUp(self):
        from apps.trips.models import Message, ProxyBuyer
        self.Message = Message
        self.buyer = make_user("s5caob@x.com")
        self.proxy_user = make_user("s5caop@x.com")
        proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p@x.com", user=self.proxy_user,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, proxy_buyer=proxy, status=Status.DEPOSIT_PAID,
            max_acceptable_date=date.today() + timedelta(days=10),
        )

    def test_buyer_box_opens_on_proxy_message(self):
        self.Message.objects.create(
            request=self.order, sender=self.proxy_user, body="Starting to purchase now",
            audience=self.Message.Audience.BUYER_PROXY,
        )
        boxes = {b["audience"]: b["open"] for b in self.order.chat_boxes(self.buyer)}
        self.assertTrue(boxes["buyer_proxy"])


class ChatBoxAutoOpenAtStep6Tests(TestCase):
    """Tabs Status Part-2, Step 6 (Note 6-1): once the Proxy marks the package
    ready (status=ITEMS_PURCHASED), the Proxy<->Carrier chat box should
    auto-open on either side whenever the counterpart posts - including on the
    Proxy's side after they've already replied (already covered by the generic
    Phase-9 chat_boxes/ChatRead mechanism - locked in here)."""

    def setUp(self):
        from apps.trips.models import Message, ProxyBuyer, TravelerOffer
        self.Message = Message
        self.buyer = make_user("s6caob@x.com")
        self.proxy_user = make_user("s6caop@x.com")
        self.carrier = make_user("s6caoc@x.com")
        proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p@x.com", user=self.proxy_user,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, proxy_buyer=proxy, status=Status.ITEMS_PURCHASED,
            max_acceptable_date=date.today() + timedelta(days=10),
        )
        TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier,
            ask_cost_per_kg=Decimal("100"), avail_kg=Decimal("5"),
            travel_date=date.today() + timedelta(days=10),
            from_city="Bangkok", from_country="Thailand",
            to_city="Jakarta", to_country="Indonesia",
            offer_status=OfferStatus.SELECTED, allocated_weight_kg=Decimal("5"),
        )

    def _open_map(self, user):
        return {b["audience"]: b["open"] for b in self.order.chat_boxes(user)}

    def test_proxy_box_opens_on_carrier_message(self):
        self.Message.objects.create(
            request=self.order, sender=self.carrier, body="Package received, weighing now",
            audience=self.Message.Audience.PROXY_CARRIER,
        )
        self.assertTrue(self._open_map(self.proxy_user)["proxy_carrier"])

    def test_carrier_box_opens_on_proxy_reply(self):
        self.Message.objects.create(
            request=self.order, sender=self.carrier, body="Package received, weighing now",
            audience=self.Message.Audience.PROXY_CARRIER,
        )
        self.Message.objects.create(
            request=self.order, sender=self.proxy_user, body="Great, ready whenever you are",
            audience=self.Message.Audience.PROXY_CARRIER,
        )
        self.assertTrue(self._open_map(self.carrier)["proxy_carrier"])


class ProfileGateTests(TestCase):
    def test_profile_complete_requires_name_and_verified_phone(self):
        u = User.objects.create_user(email="x@y.com", password="pw")
        self.assertFalse(u.profile_complete)
        u.full_name = "Jane"
        self.assertFalse(u.profile_complete)
        u.phone_verified = True
        self.assertTrue(u.profile_complete)


class OfferEditTests(TestCase):
    """Carrier can edit/withdraw a pending offer up until the buyer accepts it
    (offer_status -> SELECTED) — see TravelerOffer.can_edit."""

    def setUp(self):
        from django.urls import reverse
        from apps.trips.models import TravelerOffer
        self.reverse = reverse
        self.TravelerOffer = TravelerOffer
        self.buyer = make_user("oeb@x.com")
        self.carrier = make_user("oec@x.com")
        self.order = Order.objects.create(
            buyer=self.buyer, status=Status.OPEN,
            bid_weight_kg=Decimal("5"), estimated_weight_kg=Decimal("5"),
            max_acceptable_date=date.today() + timedelta(days=10),
        )
        self.offer = TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier,
            ask_cost_per_kg=Decimal("100"), avail_kg=Decimal("5"),
            travel_date=date.today() + timedelta(days=7),
            from_city="Bangkok", from_country="Thailand",
            to_city="Jakarta", to_country="Indonesia",
            offer_status=OfferStatus.PENDING,
        )

    def _edit_payload(self, **overrides):
        payload = {
            "ask_cost_per_kg": "150", "avail_kg": "4",
            "drop_off_address": "123 Sukhumvit Rd",
            "travel_date": (date.today() + timedelta(days=8)).strftime("%Y-%m-%d"),
            "travel_time": "12:00",
            "from_city": "Bangkok", "from_country": "Thailand",
            "to_city": "Jakarta", "to_country": "Indonesia",
        }
        payload.update(overrides)
        return payload

    def test_carrier_can_edit_while_pending(self):
        self.assertTrue(self.offer.can_edit)
        self.client.force_login(self.carrier)
        url = self.reverse("trips:offer_edit", args=[self.offer.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(url, self._edit_payload())
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.ask_cost_per_kg, Decimal("150"))
        self.assertEqual(self.offer.avail_kg, Decimal("4"))

    def test_carrier_cannot_edit_once_buyer_accepts(self):
        self.offer.offer_status = OfferStatus.SELECTED
        self.offer.allocated_weight_kg = Decimal("5")
        self.offer.save()
        self.order.recompute_status()  # mirrors offer_select view
        self.offer.refresh_from_db()
        self.assertEqual(self.order.status, Status.TAKEN)
        self.assertFalse(self.offer.can_edit)

        self.client.force_login(self.carrier)
        url = self.reverse("trips:offer_edit", args=[self.offer.pk])
        resp = self.client.post(url, self._edit_payload(ask_cost_per_kg="999"), follow=True)
        self.offer.refresh_from_db()
        # Rejected: offer must be unchanged, and view must not render the edit form.
        self.assertEqual(self.offer.ask_cost_per_kg, Decimal("100"))
        self.assertTemplateNotUsed(resp, "trips/offer_edit.html")

    def test_other_carrier_cannot_edit_someone_elses_offer(self):
        other = make_user("oeo@x.com")
        self.client.force_login(other)
        url = self.reverse("trips:offer_edit", args=[self.offer.pk])
        resp = self.client.post(url, self._edit_payload(ask_cost_per_kg="999"), follow=True)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.ask_cost_per_kg, Decimal("100"))
        self.assertTemplateNotUsed(resp, "trips/offer_edit.html")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ProxyPurchasePhotoRequiredTests(TestCase):
    """Product photos must be mandatory when the Proxy Buyer marks a package
    ready — previously the (blank=True) purchase_photo field had no server-side
    presence check, so items could be sent through with no photo at all."""

    def setUp(self):
        from apps.trips.models import ProxyBuyer

        self.buyer = make_user("ppb@x.com")
        self.proxy_user = make_user("ppp@x.com")
        proxy = ProxyBuyer.objects.create(
            name="Bangkok Proxy", country="Thailand", email="p2@x.com",
            user=self.proxy_user,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, proxy_buyer=proxy, status=Status.DEPOSIT_PAID,
        )
        self.item = RequestItem.objects.create(
            request=self.order, name="Widget", quantity=2,
            estimated_unit_cost=Decimal("10"),
        )
        self.client.force_login(self.proxy_user)
        self.url = f"/trips/orders/{self.order.pk}/proxy-purchase/"

    def _payload(self, **extra):
        payload = {
            "actual_weight_kg": "1.0",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "1",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-id": str(self.item.pk),
            "items-0-actual_quantity": "2",
            "items-0-actual_unit_cost": "10",
        }
        payload.update(extra)
        return payload

    def test_missing_product_photo_is_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        box = SimpleUploadedFile("box.jpg", b"box-bytes", content_type="image/jpeg")
        resp = self.client.post(self.url, self._payload(box_photos=box), follow=True)
        self.item.refresh_from_db()
        self.order.refresh_from_db()
        self.assertFalse(self.item.purchase_photo)
        self.assertEqual(self.order.status, Status.DEPOSIT_PAID)
        self.assertContains(resp, "product photo is required")

    def test_with_product_photo_succeeds(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        # A real (if tiny) GIF — purchase_photo is a Django ImageField, so
        # Pillow must be able to decode it, unlike the raw box_photos upload.
        gif_bytes = bytes.fromhex(
            "47494638396101000100800000000000ffffff21f90401000000002c00000000"
            "0100010000020144003b"
        )
        box = SimpleUploadedFile("box.jpg", b"box-bytes", content_type="image/jpeg")
        photo = SimpleUploadedFile("item.gif", gif_bytes, content_type="image/gif")
        resp = self.client.post(
            self.url,
            self._payload(**{"box_photos": box, "items-0-purchase_photo": photo}),
            follow=True,
        )
        self.item.refresh_from_db()
        self.order.refresh_from_db()
        self.assertTrue(bool(self.item.purchase_photo))
        self.assertEqual(self.order.status, Status.ITEMS_PURCHASED)

    def test_save_draft_persists_without_advancing_or_notifying(self):
        # "Save" persists the entered actuals but keeps the order Finalizing,
        # needs no photos, and does not notify the buyer.
        from django.core import mail
        mail.outbox = []
        resp = self.client.post(
            self.url,
            self._payload(**{"action": "save", "items-0-actual_unit_cost": "12"}),
            follow=True,
        )
        self.item.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Status.DEPOSIT_PAID)       # not advanced
        self.assertEqual(self.item.actual_unit_cost, Decimal("12"))    # saved
        self.assertFalse(self.item.purchase_photo)                     # no photo required
        self.assertEqual(len(mail.outbox), 0)                          # buyer not notified
        self.assertContains(resp, "Draft saved")

    def test_update_after_ready_persists_and_stays_ready(self):
        from django.core import mail
        self.order.status = Status.ITEMS_PURCHASED
        self.order.save(update_fields=["status"])
        mail.outbox = []
        resp = self.client.post(
            self.url,
            self._payload(**{"action": "update", "items-0-actual_unit_cost": "15"}),
            follow=True,
        )
        self.item.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Status.ITEMS_PURCHASED)    # stays ready
        self.assertEqual(self.item.actual_unit_cost, Decimal("15"))    # edit saved
        self.assertEqual(len(mail.outbox), 0)                          # no re-notify
        self.assertContains(resp, "Package details updated")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CarrierFirstMatchingTests(TestCase):
    """Carrier-First (Queuing Carrier) matching — hold ledger, guard-rail, gates,
    accept/reject/expire. See PLAN-carrier-first-orders.md."""

    def setUp(self):
        from apps.trips.models import ProxyBuyer
        self.buyer = make_user("cfb@x.com")
        self.proxy_user = make_user("cfp@x.com")
        self.carrier = make_user("cfc@x.com")
        self.proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p@x.com", user=self.proxy_user,
        )

    def _order(self, *, weight="5", status=Status.RESPONDED, cargo=False,
               deadline_days=30, proxy=True):
        return Order.objects.create(
            buyer=self.buyer,
            proxy_buyer=self.proxy if proxy else None,
            status=status,
            cargo_only=cargo,
            from_city="Bangkok", from_country="Thailand",
            to_city="Jakarta", to_country="Indonesia",
            settlement_currency="IDR",
            estimated_weight_kg=Decimal(weight),
            max_acceptable_date=date.today() + timedelta(days=deadline_days),
        )

    def _plan(self, *, traveler=None, avail="20", rate="100", travel_days=10,
              from_city="Bangkok", to_city="Jakarta", status=Status.NEW):
        return TravelPlan.objects.create(
            traveler=traveler or self.carrier,
            travel_date=date.today() + timedelta(days=travel_days),
            from_city=from_city, from_country="Thailand",
            to_city=to_city, to_country="Indonesia",
            available_weight_kg=Decimal(avail),
            shipment_currency=Currency.IDR,
            shipment_cost_per_kg=Decimal(rate),
            status=status,
        )

    # --- ledger -------------------------------------------------------------
    def test_surface_creates_hold_and_decrements_remaining(self):
        from apps.trips import matching
        order = self._order(weight="5")
        plan = self._plan(avail="20")
        created = matching.surface_matches(order, notify=False)
        self.assertEqual(len(created), 1)
        plan.refresh_from_db()
        self.assertEqual(plan.held_hold_kg, Decimal("5.00"))
        self.assertEqual(plan.carrier_first_remaining_kg, Decimal("15.00"))

    def test_guardrail_skips_plan_without_capacity(self):
        from apps.trips import matching
        plan = self._plan(avail="6")
        o1 = self._order(weight="5")
        self.assertEqual(len(matching.surface_matches(o1, notify=False)), 1)
        # Only 1kg left now — a second 5kg order must NOT be offered this carrier.
        o2 = self._order(weight="5")
        self.assertEqual(len(matching.surface_matches(o2, notify=False)), 0)

    def test_open_locked_boundary(self):
        from apps.trips import matching
        plan = self._plan(avail="5")
        order = self._order(weight="5")
        matching.surface_matches(order, notify=False)
        plan.refresh_from_db()
        self.assertEqual(plan.carrier_first_remaining_kg, Decimal("0.00"))
        self.assertTrue(plan.is_queue_locked)          # 0 < 0.99 default threshold
        self.assertEqual(plan.queue_status_label, "Locked")

    # --- matching gate ------------------------------------------------------
    def test_lead_time_gate(self):
        from apps.trips import matching
        order = self._order(weight="5")
        self._plan(avail="20", travel_days=2)          # inside the 3-day lead → skip
        self.assertEqual(len(matching.surface_matches(order, notify=False)), 0)
        self._plan(avail="20", travel_days=5)          # enough lead → match
        self.assertEqual(len(matching.surface_matches(order, notify=False)), 1)

    def test_deadline_gate_inclusive(self):
        from apps.trips import matching
        order = self._order(weight="5", deadline_days=4)
        self._plan(avail="20", travel_days=5)          # arrives after deadline → skip
        self.assertEqual(len(matching.surface_matches(order, notify=False)), 0)
        self._plan(avail="20", travel_days=4)          # arrives ON deadline → match (<=)
        self.assertEqual(len(matching.surface_matches(order, notify=False)), 1)

    def test_route_and_own_plan_excluded(self):
        from apps.trips import matching
        order = self._order(weight="5")
        self._plan(avail="20", to_city="Surabaya")     # wrong route
        self._plan(avail="20", traveler=self.buyer)    # buyer's own plan
        self.assertEqual(len(matching.surface_matches(order, notify=False)), 0)

    def test_cargo_and_non_responded_not_matchable(self):
        from apps.trips import matching
        self._plan(avail="20")
        cargo = self._order(weight="5", cargo=True, proxy=False)
        self.assertEqual(len(matching.surface_matches(cargo, notify=False)), 0)
        early = self._order(weight="5", status=Status.ESTIMATE_SENT)
        self.assertEqual(len(matching.surface_matches(early, notify=False)), 0)

    # --- accept / reject / expire ------------------------------------------
    def test_accept_creates_offer_and_advances_order(self):
        from apps.trips import matching
        from apps.trips.models import TravelerOffer
        from apps.trips.constants import MatchStatus, OfferStatus
        order = self._order(weight="5")
        plan = self._plan(avail="20", rate="120")
        match = matching.surface_matches(order, notify=False)[0]
        ok, _ = matching.accept_match(match, by_user=self.buyer)
        self.assertTrue(ok)
        order.refresh_from_db(); match.refresh_from_db()
        self.assertEqual(order.status, Status.ACCEPTED)
        self.assertEqual(match.status, MatchStatus.ACCEPTED)
        offer = TravelerOffer.objects.get(order=order)
        self.assertEqual(offer.traveler, self.carrier)
        self.assertEqual(offer.ask_cost_per_kg, Decimal("120"))
        self.assertEqual(offer.offer_status, OfferStatus.PENDING)
        self.assertEqual(match.offer_id, offer.id)
        # Accepted hold still reserves the weight.
        plan.refresh_from_db()
        self.assertEqual(plan.carrier_first_remaining_kg, Decimal("15.00"))

    def test_accept_releases_sibling_holds(self):
        from apps.trips import matching
        from apps.trips.constants import MatchStatus
        order = self._order(weight="5")
        p1 = self._plan(avail="20", rate="100")
        p2 = self._plan(avail="20", rate="90", traveler=make_user("cfc2@x.com"))
        created = matching.surface_matches(order, notify=False)
        self.assertEqual(len(created), 2)
        chosen = next(m for m in created if m.plan_id == p1.id)
        other = next(m for m in created if m.plan_id == p2.id)
        matching.accept_match(chosen, by_user=self.buyer)
        other.refresh_from_db()
        self.assertEqual(other.status, MatchStatus.REJECTED)
        p2.refresh_from_db()
        self.assertEqual(p2.carrier_first_remaining_kg, Decimal("20.00"))  # hold released

    def test_second_accept_blocked_after_order_taken(self):
        from apps.trips import matching
        order = self._order(weight="5")
        self._plan(avail="20", rate="100")
        self._plan(avail="20", rate="90", traveler=make_user("cfc3@x.com"))
        m1, m2 = matching.surface_matches(order, notify=False)
        self.assertTrue(matching.accept_match(m1, by_user=self.buyer)[0])
        ok, msg = matching.accept_match(m2, by_user=self.buyer)
        self.assertFalse(ok)                            # order no longer RESPONDED

    def test_reject_releases_hold(self):
        from apps.trips import matching
        from apps.trips.constants import MatchStatus
        order = self._order(weight="5")
        plan = self._plan(avail="20")
        match = matching.surface_matches(order, notify=False)[0]
        ok, _ = matching.reject_match(match, by_user=self.buyer)
        self.assertTrue(ok)
        match.refresh_from_db(); plan.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.REJECTED)
        self.assertEqual(plan.carrier_first_remaining_kg, Decimal("20.00"))

    def test_expire_stale_matches_releases_hold(self):
        from apps.trips import matching
        from apps.trips.constants import MatchStatus
        order = self._order(weight="5")
        plan = self._plan(avail="20")
        match = matching.surface_matches(order, notify=False)[0]
        match.window_expires_at = timezone.now() - timedelta(minutes=1)
        match.save(update_fields=["window_expires_at"])
        self.assertEqual(matching.expire_stale_matches(), 1)
        match.refresh_from_db(); plan.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.EXPIRED)
        self.assertEqual(plan.carrier_first_remaining_kg, Decimal("20.00"))

    def test_estimate_accepted_does_not_auto_surface(self):
        # 2-flow model: accepting the estimate on an unbound (Flow-1) order opens it
        # to carriers via the TravelerOffer board — it no longer auto-surfaces
        # CarrierMatch holds onto the buyer's page.
        order = self._order(weight="5")
        self._plan(avail="20")
        workflow.on_estimate_accepted(order)
        self.assertEqual(order.carrier_matches.count(), 0)

    def test_no_duplicate_surface(self):
        from apps.trips import matching
        order = self._order(weight="5")
        self._plan(avail="20")
        self.assertEqual(len(matching.surface_matches(order, notify=False)), 1)
        self.assertEqual(len(matching.surface_matches(order, notify=False)), 0)  # already held

    @override_settings(**_NO_MANIFEST_STORAGES)
    def test_board_page_renders(self):
        self._plan(avail="20")
        self.client.force_login(self.buyer)
        resp = self.client.get("/trips/carriers/queue/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Queuing Carrier")

    # --- pull path (Send Order from the board) -----------------------------
    def test_send_carrier_to_buyer_holds_fitting_orders(self):
        from apps.trips import matching
        from apps.trips.constants import MatchStatus
        order = self._order(weight="5")
        plan = self._plan(avail="20")
        created, _ = matching.send_carrier_to_buyer(plan, buyer=self.buyer)
        self.assertEqual(created, 1)
        m = order.carrier_matches.get()
        self.assertEqual(m.status, MatchStatus.PENDING)
        self.assertEqual(m.get_source_display(), "Buyer picked")   # MatchSource.PULL

    def test_send_carrier_to_buyer_no_fitting_order(self):
        from apps.trips import matching
        plan = self._plan(avail="20", to_city="Surabaya")          # wrong route
        self._order(weight="5")
        created, msg = matching.send_carrier_to_buyer(plan, buyer=self.buyer)
        self.assertEqual(created, 0)

    def test_send_carrier_does_not_oversell_across_orders(self):
        from apps.trips import matching
        plan = self._plan(avail="6")
        self._order(weight="5")
        self._order(weight="5")
        created, _ = matching.send_carrier_to_buyer(plan, buyer=self.buyer)
        self.assertEqual(created, 1)                                # only one 5kg fits in 6kg

    @override_settings(**_NO_MANIFEST_STORAGES)
    def test_home_board_renders(self):
        self._plan(avail="20", rate="150")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Queuing Carrier")
        self.assertContains(resp, "Send Order")

    # --- Flow-2 (Carrier-First): bind at order, hold at estimate -----------
    def _bound_order(self, plan, **kw):
        order = self._order(status=Status.ESTIMATE_SENT, **kw)
        order.carrier_first_plan = plan
        order.save(update_fields=["carrier_first_plan"])
        return order

    def test_reference_prefix_by_flow(self):
        # OFR- for Carrier-First (bound at creation, as order_create does), ORD- for
        # a plain Buyer-First proxy order.
        plan = self._plan(avail="20")
        bound = Order.objects.create(
            buyer=self.buyer, proxy_buyer=self.proxy, status=Status.OPEN,
            cargo_only=False, carrier_first_plan=plan,
            from_city="Bangkok", from_country="Thailand",
            to_city="Jakarta", to_country="Indonesia",
            estimated_weight_kg=Decimal("5"),
        )
        self.assertTrue(bound.reference.startswith("OFR-"), bound.reference)
        unbound = self._order(weight="5")
        self.assertTrue(unbound.reference.startswith("ORD-"), unbound.reference)

    def test_hold_for_estimate_reserves_weight_and_resets(self):
        from apps.trips import matching
        from apps.trips.constants import MatchStatus
        plan = self._plan(avail="20")
        order = self._bound_order(plan, weight="5")
        m = matching.hold_for_estimate(order)
        self.assertIsNotNone(m)
        self.assertEqual(m.status, MatchStatus.PENDING)
        self.assertEqual(m.allocated_kg, Decimal("5.00"))
        plan.refresh_from_db()
        self.assertEqual(plan.carrier_first_remaining_kg, Decimal("15.00"))
        # Re-estimate: weight → 8, same match reused (no duplicate), window reset.
        order.estimated_weight_kg = Decimal("8")
        order.save(update_fields=["estimated_weight_kg"])
        m2 = matching.hold_for_estimate(order)
        self.assertEqual(m2.pk, m.pk)
        self.assertEqual(order.carrier_matches.count(), 1)
        plan.refresh_from_db()
        self.assertEqual(plan.carrier_first_remaining_kg, Decimal("12.00"))

    def test_hold_for_estimate_noop_when_unbound(self):
        from apps.trips import matching
        order = self._order(weight="5", status=Status.ESTIMATE_SENT)   # no carrier_first_plan
        self.assertIsNone(matching.hold_for_estimate(order))
        self.assertEqual(order.carrier_matches.count(), 0)

    def test_accept_bound_carrier_advances_to_deposit(self):
        from apps.trips import matching
        from apps.trips.constants import MatchStatus, OfferStatus
        plan = self._plan(avail="20", rate="120")
        order = self._bound_order(plan, weight="5")
        matching.hold_for_estimate(order)
        ok, _ = matching.accept_bound_carrier(order, by_user=self.buyer)
        self.assertTrue(ok)
        order.refresh_from_db()
        self.assertEqual(order.status, Status.ACCEPTED)               # straight to deposit
        offer = order.traveler_offers.get()
        self.assertEqual(offer.offer_status, OfferStatus.PENDING)
        self.assertEqual(offer.ask_cost_per_kg, Decimal("120"))       # the plan's carry rate
        self.assertEqual(offer.avail_kg, Decimal("5"))
        m = order.carrier_matches.get()
        self.assertEqual(m.status, MatchStatus.ACCEPTED)
        plan.refresh_from_db()
        self.assertEqual(plan.carrier_first_remaining_kg, Decimal("15.00"))  # stays reserved

    def test_accept_bound_within_window_ok_even_if_plan_full(self):
        # The order's own live hold IS its room — a plan filled exactly by this
        # order still accepts (remaining shows 0 but the hold belongs to us).
        from apps.trips import matching
        plan = self._plan(avail="5")
        order = self._bound_order(plan, weight="5")
        matching.hold_for_estimate(order)
        plan.refresh_from_db()
        self.assertEqual(plan.carrier_first_remaining_kg, Decimal("0.00"))
        ok, _ = matching.accept_bound_carrier(order, by_user=self.buyer)
        self.assertTrue(ok)

    def test_accept_bound_carrier_blocks_oversell(self):
        # A proxy estimate larger than the plan's spare weight must be rejected at
        # accept — the floored carrier_first_remaining_kg (0) must NOT mask the
        # oversell. Regression for the accept-time capacity guard.
        from apps.trips import matching
        plan = self._plan(avail="5")
        order = self._bound_order(plan, weight="8")   # 8 > 5 capacity
        matching.hold_for_estimate(order)
        plan.refresh_from_db()
        self.assertEqual(plan.carrier_first_remaining_kg, Decimal("0.00"))  # floored, hides -3
        ok, _msg = matching.accept_bound_carrier(order, by_user=self.buyer)
        self.assertFalse(ok)
        order.refresh_from_db()
        self.assertNotEqual(order.status, Status.ACCEPTED)

    @override_settings(**_NO_MANIFEST_STORAGES)
    def test_accept_estimate_view_attaches_bound_carrier(self):
        # End-to-end: the buyer's Accept-estimate POST on a carrier-bound order
        # routes through accept_bound_carrier → ACCEPTED + a pending offer.
        from apps.trips import matching
        from apps.trips.constants import OfferStatus
        plan = self._plan(avail="20", rate="120")
        order = self._bound_order(plan, weight="5")
        matching.hold_for_estimate(order)
        self.client.force_login(self.buyer)
        resp = self.client.post(f"/trips/orders/{order.id}/estimate-accept/", follow=True)
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Status.ACCEPTED)
        self.assertEqual(
            order.traveler_offers.filter(offer_status=OfferStatus.PENDING).count(), 1)

    def test_accept_bound_blocks_when_capacity_gone_after_timeout(self):
        from apps.trips import matching
        plan = self._plan(avail="5")
        order = self._bound_order(plan, weight="5")
        m = matching.hold_for_estimate(order)
        # Timeout: this order's hold expires (weight freed), then another bound
        # order takes the whole plan before the buyer gets around to accepting.
        m.window_expires_at = timezone.now() - timedelta(minutes=1)
        m.save(update_fields=["window_expires_at"])
        matching.expire_stale_matches()
        other = self._bound_order(plan, weight="5")
        matching.hold_for_estimate(other)
        plan.refresh_from_db()
        self.assertEqual(plan.carrier_first_remaining_kg, Decimal("0.00"))
        ok, msg = matching.accept_bound_carrier(order, by_user=self.buyer)
        self.assertFalse(ok)
        self.assertIn("spare weight", msg)
        order.refresh_from_db()
        self.assertEqual(order.status, Status.ESTIMATE_SENT)          # not advanced
