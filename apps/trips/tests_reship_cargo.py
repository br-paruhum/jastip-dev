"""Cargo reshipment runs through the shared Buy Order card (_reship_tab.html).

The card renders for a proxy Order and for a cargo leg, so the risk is a name or
an endpoint that only lines up on one of them. These drive the leg flow end to
end through the real views and assert on what the buyer/carrier actually see.
"""
import re
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.trips.constants import OfferStatus
from apps.trips.models import LegPayment, LegStatus, LegTransaction, Order, TravelerOffer

User = get_user_model()


def _png():
    # 1x1 PNG — smallest thing the ImageField will accept.
    return SimpleUploadedFile(
        "p.png",
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
        b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
        b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
        content_type="image/png",
    )


class CargoReshipTabTests(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(
            email="b@x.com", password="pw", full_name="Bea Buyer",
            phone_country_code="+62", phone_number="81200000001", phone_verified=True,
            buyer_invoice_address="Jl. Buyer 1",
        )
        self.carrier = User.objects.create_user(
            email="c@x.com", password="pw", full_name="Cari Carrier",
            phone_country_code="+62", phone_number="81200000002", phone_verified=True,
            traveler_bank_details="BCA 123 / Cari",
        )
        self.order = Order.objects.create(
            buyer=self.buyer, cargo_only=True, settlement_currency="IDR",
            bid_weight_kg=Decimal("2.5"), from_country="Germany", to_country="Indonesia",
            delivery_preference="reship",
            receiver_details="Budi Santoso\nHauptstrasse 45\nTroisdorf 53840",
        )
        self.leg = TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier, offer_status=OfferStatus.SELECTED,
            ask_cost_per_kg=Decimal("295000"), avail_kg=Decimal("10"),
            allocated_weight_kg=Decimal("2.5"),
            agreed_weight_kg=Decimal("3.1"), travel_date=date.today() + timedelta(days=3),
            fulfillment_method="reship", leg_status=LegStatus.RESHIP_REQUESTED,
            reshipment_address="Jl. Buyer 1",
        )
        self.order.recompute_status()

    def _buyer_html(self):
        self.order.refresh_from_db()
        return render_to_string(
            "trips/_order_detail_body.html",
            {"bf_order": self.order, "is_order_buyer": True, "is_order_proxy": False},
        )

    def _carrier_html(self):
        self.leg.refresh_from_db()
        return render_to_string("trips/_offer_detail_body.html", {"leg_offer": self.leg})

    def test_buyer_sees_courier_input_and_carrier_bank_details(self):
        html = self._buyer_html()
        self.assertIn("Reshipment", html)
        self.assertIn("Preferred Courier", html)
        self.assertIn("BCA 123 / Cari", html)  # carrier's profile bank details
        self.assertIn("Save Preferred Courier", html)

    def test_carrier_sees_receiver_address_and_cost_form(self):
        # Cargo ships to the named receiver (as on the customs invoice), never to
        # the buyer — showing "Bea Buyer" here would send the package to Jakarta.
        html = self._carrier_html()
        self.assertIn("Receiver Address", html)
        self.assertIn("Budi Santoso", html)
        self.assertNotIn("Buyer's Reshipment Address", html)
        self.assertNotIn("Bea Buyer", html)
        self.assertIn("Send Cost", html)

    def test_proxy_order_still_addresses_the_buyer(self):
        # No receiver on a proxy order — the buyer IS the receiver.
        self.order.cargo_only = False
        self.order.receiver_details = ""
        self.order.save()
        self.assertEqual(self.order.reship_receiver_details, "")

    def test_full_leg_reship_round_trip(self):
        # 1. Buyer saves their preferred courier (order-level endpoint, leg card).
        self.client.force_login(self.buyer)
        self.client.post(
            reverse("trips:request_set_courier", args=[self.order.id]),
            {"preferred_courier": "JNE express"},
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.preferred_courier, "JNE express")
        self.assertIn("JNE express", self._carrier_html())  # carrier sees it

        # 2. Carrier sends the reshipment cost.
        self.client.force_login(self.carrier)
        self.client.post(
            reverse("trips:leg_reship_cost", args=[self.leg.id]),
            {"reshipment_cost_amount": "250000"},
        )
        self.leg.refresh_from_db()
        self.assertEqual(self.leg.leg_status, LegStatus.RESHIP_COST_SENT)
        self.assertIn("Upload Transfer Proof", self._buyer_html())

        # 3. Buyer uploads the transfer proof.
        self.client.force_login(self.buyer)
        self.client.post(
            reverse("trips:leg_reship_proof", args=[self.leg.id]),
            {"reshipment_proof": _png()},
        )
        self.leg.refresh_from_db()
        self.assertTrue(self.leg.reshipment_proof)
        self.assertIn("View Transfer Proof", self._carrier_html())

        # 4. Carrier ships with an AWB document and no AWB number (Buy Order format).
        self.client.force_login(self.carrier)
        self.client.post(
            reverse("trips:leg_reship_ship", args=[self.leg.id]),
            {"awb_document": _png()},
        )
        self.leg.refresh_from_db()
        self.assertEqual(self.leg.leg_status, LegStatus.RESHIPPING)
        self.assertTrue(self.leg.awb_document)

        # 5. Buyer sees the AWB + Confirm Received, and closes the leg out.
        buyer_html = self._buyer_html()
        self.assertIn("View AWB", buyer_html)
        self.assertIn("Confirm Received", buyer_html)
        self.client.force_login(self.buyer)
        self.client.post(reverse("trips:leg_clear", args=[self.leg.id]))
        self.leg.refresh_from_db()
        self.assertEqual(self.leg.leg_status, LegStatus.CLEAR)

    def _settle_second_deposit(self):
        # The duty receipt is gated on the buyer's second deposit clearing.
        txn, _ = LegTransaction.objects.get_or_create(leg=self.leg)
        LegPayment.objects.create(
            transaction=txn, direction=LegPayment.Direction.INBOUND,
            kind=LegPayment.Kind.BALANCE, amount=self.leg.extra_due,
            status=LegPayment.PaymentStatus.VERIFIED,
        )
        self.leg.refresh_from_db()
        self.assertTrue(self.leg.balance_settled)

    def test_duty_receipt_honors_the_order_time_delivery_choice(self):
        # Buy Order rule (_honor_pickup_preference): the buyer chose at order
        # time, so uploading the duty receipt moves the leg on by itself.
        self.leg.leg_status = LegStatus.PACKAGE_ARRIVED
        self.leg.fulfillment_method = ""
        self.leg.custom_fare_amount = Decimal("65")
        self.leg.custom_fare_currency = "IDR"
        self.leg.save()
        self._settle_second_deposit()

        self.client.force_login(self.carrier)
        self.client.post(
            reverse("trips:leg_custom_fare_proof", args=[self.leg.id]),
            {"custom_fare_proof": _png()},
        )
        self.leg.refresh_from_db()
        self.assertEqual(self.leg.fulfillment_method, "reship")
        self.assertEqual(self.leg.leg_status, LegStatus.RESHIP_REQUESTED)
        self.assertTrue(self.leg.is_reship_flow)
        # ... and both sides now show the Buy Order card + the right notes.
        self.assertIn("[10A-0T]", self._carrier_html())
        self.assertIn("<h3>Reshipment</h3>", self._carrier_html())
        self.assertIn("[10A-0B]", self._buyer_html())
        self.assertIn("<h3>Reshipment</h3>", self._buyer_html())

    def test_pickup_preference_goes_to_ready_for_pickup_instead(self):
        self.order.delivery_preference = "pickup"
        self.order.save()
        self.leg.leg_status = LegStatus.PACKAGE_ARRIVED
        self.leg.fulfillment_method = ""
        self.leg.save()
        self._settle_second_deposit()

        self.client.force_login(self.carrier)
        self.client.post(
            reverse("trips:leg_custom_fare_proof", args=[self.leg.id]),
            {"custom_fare_proof": _png()},
        )
        self.leg.refresh_from_db()
        self.assertEqual(self.leg.leg_status, LegStatus.READY_FOR_PICKUP)
        self.assertFalse(self.leg.is_reship_flow)

    def test_notes_follow_the_leg_not_the_empty_order_fields(self):
        # Cargo's transfer proof lives on the leg; the Notes used to read the
        # order-level field and so never left the "please pay" step.
        self.leg.leg_status = LegStatus.RESHIP_COST_SENT
        self.leg.reshipment_cost_amount = Decimal("250000")
        self.leg.save()
        self.order.recompute_status()
        self.assertIn("[12A-0B]", self._buyer_html())      # please pay + upload proof
        self.assertIn("[12A-0T]", self._carrier_html())    # carrier waits for proof

        self.leg.reshipment_proof = _png()
        self.leg.save()
        self.assertIn("[13A-0B]", self._buyer_html())      # proof sent, wait for AWB
        self.assertIn("[13A-0T]", self._carrier_html())    # carrier can ship

    def test_dropoff_card_does_not_duplicate_the_reship_controls(self):
        # One Reshipment card, one courier field — the Drop-Off card's old inline
        # copy of the reship steps must not come back alongside it.
        html = self._buyer_html()
        self.assertNotIn("Next Step", html)
        self.assertEqual(html.count("<h3>Reshipment</h3>"), 1)
        self.assertEqual(html.count("<label>Preferred Courier</label>"), 1)


class NotesMatchSpecTests(TestCase):
    """The Notes copy is specified in
    proxybuying-obsidian/AI-Context/references/NotesTabContents_BuyerComeFirst.xlsx.

    Anchors are hand-typed and had drifted ([10.1A-0B] where the sheet says
    [10A.0B]), so pin them to the sheet. Copy itself is NOT pinned — the wording
    is hand-tuned in the template and deliberately differs in places; only the
    step-10 pair is checked, because that one was written from memory once.
    """

    XLSX = "proxybuying-obsidian/AI-Context/references/NotesTabContents_BuyerComeFirst.xlsx"

    def _sheet(self):
        openpyxl = __import__("openpyxl")
        return list(
            openpyxl.load_workbook(self.XLSX)["Buyer-First Flow"].iter_rows(values_only=True)
        )

    def test_reship_anchors_exist(self):
        tpl = open("templates/trips/_order_notes.html").read()
        for anchor in ["[10A-0B]", "[10A-0T]", "[11A-0B]", "[11A-0T]",
                       "[12A-0B]", "[12A-0T]", "[13A-0B]", "[13A-0T]",
                       "[14A-0B]", "[14A-0T]"]:
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, tpl)
        # The pre-rename labels must not come back.
        self.assertNotIn("[10.1A-0B]", tpl)
        self.assertNotIn("[10.1A-0T]", tpl)

    def test_step10_copy_is_the_spreadsheets_not_invented(self):
        rows = self._sheet()
        tpl = " ".join(open("templates/trips/_order_notes.html").read().split())
        for col, anchor in ((4, "[10A-0B]"), (6, "[10A-0T]")):
            with self.subTest(anchor=anchor):
                spec = " ".join(str(rows[52][col]).split()).strip().rstrip(".")
                self.assertIn(spec[:45], tpl)


class DropOffFoldTests(TestCase):
    """Drop-Off Address is open while the buyer still needs the address, and
    collapses once the package is handed over (a leg gets a leg_status only at
    drop-off, so that's the switch)."""

    def setUp(self):
        self.buyer = User.objects.create_user(
            email="b2@x.com", password="pw", full_name="Bea Buyer",
            phone_country_code="+62", phone_number="81200000003", phone_verified=True,
        )
        self.carrier = User.objects.create_user(
            email="c2@x.com", password="pw", full_name="Cari Carrier",
            phone_country_code="+62", phone_number="81200000004", phone_verified=True,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, cargo_only=True, settlement_currency="IDR",
            bid_weight_kg=Decimal("2.5"), from_country="Germany", to_country="Indonesia",
        )
        self.leg = TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier, offer_status=OfferStatus.SELECTED,
            ask_cost_per_kg=Decimal("295000"), avail_kg=Decimal("10"),
            allocated_weight_kg=Decimal("2.5"), travel_date=date.today() + timedelta(days=3),
            drop_off_address="Jl. Carrier 9", leg_status="",
        )
        # Deposit must clear or the card doesn't render at all.
        txn = LegTransaction.objects.create(leg=self.leg)
        LegPayment.objects.create(
            transaction=txn, direction=LegPayment.Direction.INBOUND,
            kind=LegPayment.Kind.DEPOSIT, amount=self.leg.deposit_due,
            status=LegPayment.PaymentStatus.VERIFIED,
        )

    def _fold(self):
        self.order.refresh_from_db()
        html = render_to_string(
            "trips/_order_detail_body.html",
            {"bf_order": self.order, "is_order_buyer": True, "is_order_proxy": False},
        )
        m = re.search(r'<details class="card fold mt-2"( open)?>\s*<summary><h3>Drop-Off Address</h3>', html)
        self.assertIsNotNone(m, "Drop-Off Address is not a fold card")
        return bool(m.group(1))

    def test_open_before_handover(self):
        self.assertTrue(self.order.awaiting_dropoff)
        self.assertTrue(self._fold(), "must be open while the buyer still needs the address")

    def test_collapsed_once_handed_over(self):
        self.leg.leg_status = LegStatus.PACKAGE_DROPPED_OFF
        self.leg.save()
        self.assertFalse(self.order.awaiting_dropoff)
        self.assertFalse(self._fold(), "must collapse once the package is handed over")


class CargoPayoutReleaseTests(TestCase):
    """Cargo pays out per leg. release_disbursement only knows the order-level
    Transaction that cargo never has, so the leg has its own release."""

    def setUp(self):
        self.buyer = User.objects.create_user(
            email="b3@x.com", password="pw", full_name="Bea Buyer",
            phone_country_code="+62", phone_number="81200000005", phone_verified=True,
        )
        self.carrier = User.objects.create_user(
            email="c3@x.com", password="pw", full_name="Cari Carrier",
            phone_country_code="+62", phone_number="81200000006", phone_verified=True,
        )
        self.staff = User.objects.create_user(
            email="s@x.com", password="pw", full_name="Stella Staff",
            phone_country_code="+62", phone_number="81200000007", phone_verified=True,
            is_staff=True,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, cargo_only=True, settlement_currency="IDR",
            bid_weight_kg=Decimal("2.5"), from_country="Germany", to_country="Indonesia",
            delivery_preference="reship",
        )
        self.leg = TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier, offer_status=OfferStatus.SELECTED,
            ask_cost_per_kg=Decimal("295000"), avail_kg=Decimal("10"),
            allocated_weight_kg=Decimal("2.5"), agreed_weight_kg=Decimal("3.1"),
            travel_date=date.today() + timedelta(days=3),
            fulfillment_method="reship", leg_status=LegStatus.CLEAR,
        )
        LegTransaction.objects.create(leg=self.leg)

    def _carrier_html(self):
        self.leg.refresh_from_db()
        return render_to_string(
            "trips/_offer_detail_body.html", {"leg_offer": self.leg, "user": self.staff}
        )

    def test_staff_release_stamps_leg_and_records_the_payout(self):
        self.assertTrue(self.leg.payout_disbursable)
        self.assertIn("Admin — release payout", self._carrier_html())

        self.client.force_login(self.staff)
        self.client.post(
            reverse("trips:leg_release_payout", args=[self.leg.id]), {"proof": _png()}
        )
        self.leg.refresh_from_db()
        self.assertIsNotNone(self.leg.payout_paid_at)
        # Ledger row, so the money movement is recorded like every other one.
        payout = self.leg.transaction.payments.get(kind=LegPayment.Kind.PAYOUT)
        self.assertEqual(payout.amount, self.leg.transaction.payout_to_traveler)
        self.assertEqual(payout.direction, LegPayment.Direction.OUTBOUND)
        self.assertTrue(self.leg.payout_proof_url)
        # Form is gone, proof link is up, and the note moves to step 16.
        html = self._carrier_html()
        self.assertNotIn("Admin — release payout", html)
        self.assertIn("View Transfer Proof", html)
        notes = render_to_string(
            "trips/_order_notes.html",
            {"leg_offer": self.leg, "note_role": "carrier", "note_order": self.order},
        )
        self.assertIn("[16A-0T]", notes)
        self.assertNotIn("[15A-1T]", notes)

    def test_release_moves_the_buyer_note_to_step_16(self):
        self.client.force_login(self.staff)
        self.client.post(
            reverse("trips:leg_release_payout", args=[self.leg.id]), {"proof": _png()}
        )
        self.order.refresh_from_db()
        notes = render_to_string(
            "trips/_order_notes.html",
            {"note_order": self.order, "note_role": "order",
             "is_order_buyer": True, "is_order_proxy": False},
        )
        self.assertIn("[16A-0B]", notes)
        self.assertIn("Transaction Completed", notes)
        self.assertNotIn("[15A-1B]", notes)
        self.assertNotIn("[15A-2B]", notes)
        # Cargo has no proxy buyer — that clause must not leak in from the proxy copy.
        self.assertNotIn("proxy buyer's", notes)

    def test_payout_does_not_disturb_the_buyer_balance(self):
        self.client.force_login(self.staff)
        self.client.post(
            reverse("trips:leg_release_payout", args=[self.leg.id]), {"proof": _png()}
        )
        self.leg.refresh_from_db()
        # kind=PAYOUT is OUTBOUND and must not read as a buyer deposit/refund.
        self.assertEqual(self.leg.deposit_paid_amount, Decimal("0.00"))
        self.assertEqual(self.leg.balance_paid_amount, Decimal("0.00"))
        self.assertEqual(self.leg.total_refunded, Decimal("0.00"))

    def test_non_staff_cannot_release(self):
        self.client.force_login(self.carrier)
        self.client.post(
            reverse("trips:leg_release_payout", args=[self.leg.id]), {"proof": _png()}
        )
        self.leg.refresh_from_db()
        self.assertIsNone(self.leg.payout_paid_at)

    def test_cannot_release_twice(self):
        self.client.force_login(self.staff)
        url = reverse("trips:leg_release_payout", args=[self.leg.id])
        self.client.post(url, {"proof": _png()})
        self.leg.refresh_from_db()
        first = self.leg.payout_paid_at
        self.client.post(url, {"proof": _png()})
        self.leg.refresh_from_db()
        self.assertEqual(self.leg.payout_paid_at, first)
        self.assertEqual(self.leg.transaction.payments.filter(kind=LegPayment.Kind.PAYOUT).count(), 1)

    def test_cannot_release_before_the_buyer_clears(self):
        self.leg.leg_status = LegStatus.RESHIPPING
        self.leg.save()
        self.client.force_login(self.staff)
        self.client.post(
            reverse("trips:leg_release_payout", args=[self.leg.id]), {"proof": _png()}
        )
        self.leg.refresh_from_db()
        self.assertIsNone(self.leg.payout_paid_at)


class PendingDisbursementPanelTests(TestCase):
    """The dashboard's Pending Disbursement panel asked only the order-level
    traveler_payout_disbursable, so cargo payouts never appeared — and its form
    hardcoded the order-level endpoint, which would leave the leg unpaid."""

    def setUp(self):
        self.buyer = User.objects.create_user(
            email="b4@x.com", password="pw", full_name="Bea Buyer",
            phone_country_code="+62", phone_number="81200000008", phone_verified=True,
        )
        self.carrier = User.objects.create_user(
            email="c4@x.com", password="pw", full_name="Cari Carrier",
            phone_country_code="+62", phone_number="81200000009", phone_verified=True,
        )
        self.su = User.objects.create_superuser(email="su@x.com", password="pw")
        self.order = Order.objects.create(
            buyer=self.buyer, cargo_only=True, settlement_currency="IDR",
            bid_weight_kg=Decimal("2.5"), from_country="Germany", to_country="Indonesia",
        )
        self.leg = TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier, offer_status=OfferStatus.SELECTED,
            ask_cost_per_kg=Decimal("295000"), avail_kg=Decimal("10"),
            allocated_weight_kg=Decimal("2.5"), agreed_weight_kg=Decimal("3.1"),
            travel_date=date.today() + timedelta(days=3), leg_status=LegStatus.CLEAR,
        )
        LegTransaction.objects.create(leg=self.leg)

    def _rows(self):
        from apps.accounts.views import _pending_disbursements
        return _pending_disbursements(self.su)

    def test_cleared_cargo_leg_is_listed_against_the_leg_endpoint(self):
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        entry = rows[0]["pending"][0]
        self.assertEqual(entry["url_name"], "trips:leg_release_payout")
        self.assertEqual(entry["target_id"], self.leg.id)
        self.assertEqual(entry["amount"], self.leg.transaction.payout_to_traveler)
        self.assertEqual(entry["recipient"], "Cari Carrier")

    def test_released_leg_drops_off_the_list(self):
        self.leg.payout_paid_at = timezone.now()
        self.leg.save()
        self.assertEqual(self._rows(), [])

    def test_uncleared_leg_is_not_listed(self):
        self.leg.leg_status = LegStatus.RESHIPPING
        self.leg.save()
        self.assertEqual(self._rows(), [])

    def test_panel_is_superuser_only(self):
        from apps.accounts.views import _pending_disbursements
        self.assertEqual(_pending_disbursements(self.buyer), [])
