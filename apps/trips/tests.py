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
        self.assertTrue(self.req.reference.startswith("PRX-"))

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
