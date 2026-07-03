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
