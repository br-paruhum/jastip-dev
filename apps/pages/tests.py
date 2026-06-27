from django.test import TestCase, override_settings
from django.urls import reverse

# Render templates without the manifest static storage (no collectstatic in tests).
_NO_MANIFEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class HowToPageTests(TestCase):
    """Task 13: /how-to/ is a static informational template, no longer driven by
    a CMS SitePage — it must render even with no SitePage rows in the database."""

    def test_renders_without_sitepage(self):
        resp = self.client.get(reverse("pages:how_to"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "pages/how_to.html")
        for needle in (
            "How ProxyBuying works",
            # lane headers (Buyer | Proxy Buyer | Carrier)
            "Proxy Buyer", "Carrier",
            # step copy + screenshots live in the inline infographic script
            "Proxy Buyers List", "Queuing Cargo",
            "/static/img/howto/buyer-start.webp",
            "/static/img/howto/carrier-start.webp",
        ):
            self.assertContains(resp, needle)
