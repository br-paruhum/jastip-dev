from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.trips.constants import Status
from apps.trips.models import Order, ProxyBuyer

User = get_user_model()

_NO_MANIFEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def make_user(email):
    return User.objects.create_user(
        email=email, password="pw", full_name=email.split("@")[0],
        phone_country_code="+62", phone_number="81200000000", phone_verified=True,
    )


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class OrderDetailPaymentTermsNotesFoldTests(TestCase):
    """Tabs Status Part-1, Step 1 (Buyer sends order): the Payment Terms and
    Notes folds on the order-detail panel (accounts:profile?order=<id>) should
    open expanded on the Buyer's first look, and stay hidden from the Proxy
    Buyer until they've sent their estimate."""

    def setUp(self):
        from django.urls import reverse
        self.reverse = reverse
        self.buyer = make_user("tsb@x.com")
        self.proxy_user = make_user("tsp@x.com")
        self.proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p@x.com", user=self.proxy_user,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, proxy_buyer=self.proxy, status=Status.OPEN,
            max_acceptable_date=date.today() + timedelta(days=10),
        )

    def _get(self, user):
        self.client.force_login(user)
        return self.client.get(self.reverse("accounts:profile") + f"?order={self.order.pk}#order-detail")

    def test_buyer_sees_payment_terms_and_notes_open_at_step1(self):
        resp = self._get(self.buyer)
        content = resp.content.decode()
        self.assertIn("Payment Terms", content)
        self.assertIn("Notes", content)
        # Both folds should render with the `open` attribute at this first step.
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Payment Terms</h3>')
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Notes</h3>')

    def test_proxy_does_not_see_payment_terms_or_notes_before_estimate(self):
        resp = self._get(self.proxy_user)
        content = resp.content.decode()
        self.assertNotIn("<h3>Payment Terms</h3>", content)
        self.assertNotIn("<h3>Notes</h3>", content)

    def test_proxy_sees_payment_terms_and_notes_after_sending_estimate(self):
        self.order.status = Status.ESTIMATE_SENT
        self.order.save()
        resp = self._get(self.proxy_user)
        content = resp.content.decode()
        self.assertIn("<h3>Payment Terms</h3>", content)
        self.assertIn("<h3>Notes</h3>", content)


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class OrderDetailStep2FoldTests(TestCase):
    """Tabs Status Part-1, Step 2 (Proxy sends estimate, status=ESTIMATE_SENT):
    Payment Terms opens expanded on the Proxy's first look (closes for the
    Buyer, since their first look was Step 1); Notes stays open for both."""

    def setUp(self):
        from django.urls import reverse
        self.reverse = reverse
        self.buyer = make_user("s2b@x.com")
        self.proxy_user = make_user("s2p@x.com")
        self.proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p2@x.com", user=self.proxy_user,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, proxy_buyer=self.proxy, status=Status.ESTIMATE_SENT,
            max_acceptable_date=date.today() + timedelta(days=10),
        )

    def _get(self, user):
        self.client.force_login(user)
        return self.client.get(self.reverse("accounts:profile") + f"?order={self.order.pk}#order-detail")

    def test_buyer_payment_terms_closed_but_notes_open_at_step2(self):
        resp = self._get(self.buyer)
        content = resp.content.decode()
        self.assertRegex(content, r'<details class="card fold mt-2">\s*<summary><h3>Payment Terms</h3>')
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Notes</h3>')

    def test_proxy_payment_terms_and_notes_open_at_step2(self):
        resp = self._get(self.proxy_user)
        content = resp.content.decode()
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Payment Terms</h3>')
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Notes</h3>')


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class OrderDetailStep3FoldTests(TestCase):
    """Tabs Status Part-1, Step 3 (Buyer accepts the estimate, status=RESPONDED,
    set by trips.views.proxy_estimate_accept): Payment Terms stays closed for
    both roles (each already had their first-look moment); Notes opens for
    both Buyer and Proxy."""

    def setUp(self):
        from django.urls import reverse
        self.reverse = reverse
        self.buyer = make_user("s3b@x.com")
        self.proxy_user = make_user("s3p@x.com")
        self.proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p3@x.com", user=self.proxy_user,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, proxy_buyer=self.proxy, status=Status.RESPONDED,
            max_acceptable_date=date.today() + timedelta(days=10),
        )

    def _get(self, user):
        self.client.force_login(user)
        return self.client.get(self.reverse("accounts:profile") + f"?order={self.order.pk}#order-detail")

    def test_buyer_notes_open_payment_terms_closed_at_step3(self):
        resp = self._get(self.buyer)
        content = resp.content.decode()
        self.assertRegex(content, r'<details class="card fold mt-2">\s*<summary><h3>Payment Terms</h3>')
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Notes</h3>')

    def test_proxy_notes_open_payment_terms_closed_at_step3(self):
        resp = self._get(self.proxy_user)
        content = resp.content.decode()
        self.assertRegex(content, r'<details class="card fold mt-2">\s*<summary><h3>Payment Terms</h3>')
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Notes</h3>')


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class OfferDetailStep4FoldTests(TestCase):
    """Tabs Status Part-1, Step 4 (Carrier submits offer, order.status stays
    RESPONDED - offer_create calls recompute_status() which keeps it there
    since the offer is still pending): on the Carrier's own offer-detail panel
    (accounts:profile?offer=<id>), Payment Terms should open on their first
    look (still awaiting the buyer's decision) - Notes is already always open
    there (hardcoded), unaffected by this step."""

    def setUp(self):
        from decimal import Decimal
        from django.urls import reverse
        from apps.trips.models import TravelerOffer
        from apps.trips.constants import OfferStatus
        self.reverse = reverse
        self.buyer = make_user("s4b@x.com")
        self.proxy_user = make_user("s4p@x.com")
        self.carrier = make_user("s4c@x.com")
        proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p4@x.com", user=self.proxy_user,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, proxy_buyer=proxy, status=Status.RESPONDED,
            max_acceptable_date=date.today() + timedelta(days=10),
        )
        self.offer = TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier, ask_cost_per_kg=Decimal("100"), avail_kg=Decimal("5"),
            travel_date=date.today() + timedelta(days=10),
            from_city="Bangkok", from_country="Thailand", to_city="Jakarta", to_country="Indonesia",
            offer_status=OfferStatus.PENDING,
        )

    def test_carrier_sees_payment_terms_open_while_offer_pending(self):
        self.client.force_login(self.carrier)
        resp = self.client.get(self.reverse("accounts:profile") + f"?offer={self.offer.pk}#offer-detail")
        content = resp.content.decode()
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Payment Terms</h3>')


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class OrderDetailStep5FoldTests(TestCase):
    """Tabs Status Part-1, Step 5a/5b/5c: buyer accepts the carrier's offer
    (status=ACCEPTED, deposit_pending=False) -> submits transfer proof
    (status=ACCEPTED, deposit_pending=True) -> admin verifies
    (status=DEPOSIT_PAID). Buyer's Notes: open/closed/open across those three
    states (Note 5.1 - hide the pay form while awaiting verification). Proxy's
    Notes: stays open throughout. Payments fold: buyer-only, open once shown."""

    def setUp(self):
        from django.urls import reverse
        from apps.trips.models import Payment, Transaction
        self.reverse = reverse
        self.Payment = Payment
        self.buyer = make_user("s5b@x.com")
        self.proxy_user = make_user("s5p@x.com")
        self.proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p5@x.com", user=self.proxy_user,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, proxy_buyer=self.proxy, status=Status.ACCEPTED,
            max_acceptable_date=date.today() + timedelta(days=10),
        )
        self.tx = Transaction.objects.create(request=self.order)

    def _get(self, user):
        self.client.force_login(user)
        return self.client.get(self.reverse("accounts:profile") + f"?order={self.order.pk}#order-detail")

    def test_step5a_buyer_notes_open_no_payment_yet(self):
        resp = self._get(self.buyer)
        content = resp.content.decode()
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Notes</h3>')
        self.assertIn("Submit Transfer Proof", content)
        self.assertNotIn("<h3>Payments</h3>", content)  # no payment record yet

    def test_step5a_proxy_notes_open(self):
        resp = self._get(self.proxy_user)
        content = resp.content.decode()
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Notes</h3>')

    def test_step5b_buyer_notes_closed_shows_awaiting_verification(self):
        self.Payment.objects.create(
            transaction=self.tx, direction=self.Payment.Direction.INBOUND, kind=self.Payment.Kind.DEPOSIT,
            currency=self.order.currency, amount=self.order.deposit_due, status=self.Payment.PaymentStatus.PENDING,
        )
        resp = self._get(self.buyer)
        content = resp.content.decode()
        self.assertRegex(content, r'<details class="card fold mt-2">\s*<summary><h3>Notes</h3>')
        self.assertIn("awaiting admin verification of your deposit", content)
        self.assertNotIn('<div style="margin-bottom:10px"><input type="file" name="proof"', content)  # Note 5.1
        # Payments fold: buyer-only, open once a payment exists.
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Payments</h3>')

    def test_step5b_proxy_never_sees_payments_fold(self):
        self.Payment.objects.create(
            transaction=self.tx, direction=self.Payment.Direction.INBOUND, kind=self.Payment.Kind.DEPOSIT,
            currency=self.order.currency, amount=self.order.deposit_due, status=self.Payment.PaymentStatus.PENDING,
        )
        resp = self._get(self.proxy_user)
        content = resp.content.decode()
        self.assertNotIn("<h3>Payments</h3>", content)
        # Proxy's Notes stays open even while deposit is pending.
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Notes</h3>')
        # Regression: the deposit_pending branch is role-neutral (no status
        # check gates it), so it used to leak the buyer's own "upload proof"
        # text/button/link to the Proxy too - Proxy should see the generic
        # waiting text instead.
        self.assertNotIn("Your uploaded proof:", content)
        self.assertNotIn("Submit Transfer Proof", content)
        self.assertIn("Buyer accepted the shipment cost. Waiting for the Buyer's deposit", content)

    def test_step5c_buyer_notes_open_after_verification_clay_button(self):
        self.Payment.objects.create(
            transaction=self.tx, direction=self.Payment.Direction.INBOUND, kind=self.Payment.Kind.DEPOSIT,
            currency=self.order.currency, amount=self.order.deposit_due, status=self.Payment.PaymentStatus.VERIFIED,
            proof="deposit_proofs/x.jpg",
        )
        self.order.status = Status.DEPOSIT_PAID
        self.order.save()
        resp = self._get(self.buyer)
        content = resp.content.decode()
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Notes</h3>')
        # Note 5.3: the View Transfer Proof link uses the clay/primary button, not outline.
        self.assertIn('class="btn btn-primary btn-sm">View Transfer Proof</a>', content)

    def test_step5c_proxy_notes_open(self):
        self.order.status = Status.DEPOSIT_PAID
        self.order.save()
        resp = self._get(self.proxy_user)
        content = resp.content.decode()
        self.assertRegex(content, r'<details class="card fold mt-2" open>\s*<summary><h3>Notes</h3>')


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class OfferDetailStep5NotesTests(TestCase):
    """Note 5.2: on the Carrier's own offer-detail panel, once the order is
    ACCEPTED, Notes wording should distinguish 5a (awaiting the buyer's
    deposit) from 5b (deposit_pending - proof already submitted)."""

    def setUp(self):
        from decimal import Decimal
        from django.urls import reverse
        from apps.trips.models import TravelerOffer, Payment, Transaction
        from apps.trips.constants import OfferStatus
        self.reverse = reverse
        self.Payment = Payment
        self.buyer = make_user("s5nb@x.com")
        self.carrier = make_user("s5nc@x.com")
        proxy_user = make_user("s5np@x.com")
        proxy = ProxyBuyer.objects.create(
            name="BKK Proxy", country="Thailand", email="p5n@x.com", user=proxy_user,
        )
        self.order = Order.objects.create(
            buyer=self.buyer, proxy_buyer=proxy, status=Status.ACCEPTED,
            max_acceptable_date=date.today() + timedelta(days=10),
        )
        self.offer = TravelerOffer.objects.create(
            order=self.order, traveler=self.carrier, ask_cost_per_kg=Decimal("100"), avail_kg=Decimal("5"),
            travel_date=date.today() + timedelta(days=10),
            from_city="Bangkok", from_country="Thailand", to_city="Jakarta", to_country="Indonesia",
            # order_accept (Step 5a) never selects the leg - Flow-1 proxy
            # offers stay PENDING through the whole carry flow (no leg).
            offer_status=OfferStatus.PENDING,
        )
        self.tx = Transaction.objects.create(request=self.order)

    def _get(self):
        self.client.force_login(self.carrier)
        return self.client.get(self.reverse("accounts:profile") + f"?offer={self.offer.pk}#offer-detail")

    def test_carrier_sees_awaiting_deposit_before_proof_submitted(self):
        content = self._get().content.decode()
        self.assertIn("Buyer accepted your offer rate — awaiting for Buyer's deposit.", content)

    def test_carrier_sees_verification_wording_once_proof_submitted(self):
        self.Payment.objects.create(
            transaction=self.tx, direction=self.Payment.Direction.INBOUND, kind=self.Payment.Kind.DEPOSIT,
            currency=self.order.currency, amount=self.order.deposit_due, status=self.Payment.PaymentStatus.PENDING,
        )
        content = self._get().content.decode()
        self.assertIn("Buyer submit transfer proof, waiting for admin fund verification.", content)
