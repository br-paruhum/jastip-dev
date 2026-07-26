from django.test import TestCase, override_settings
from django.urls import reverse

from apps.pages.views import _parse_qa

# Render templates without the manifest static storage (no collectstatic in tests).
_NO_MANIFEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class HowToPageTests(TestCase):
    """The How-To guide is a static template (no CMS SitePage). Legacy /how-to/
    now permanently redirects to the buyer page, which must render on its own."""

    def test_legacy_how_to_redirects_to_buyer_page(self):
        resp = self.client.get(reverse("pages:how_to"), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertRedirects(
            resp, reverse("pages:how_to_for_buyer"), status_code=301
        )
        self.assertTemplateUsed(resp, "pages/how_to_for_buyer.html")
        for needle in ("How It Works - For Buyer", "Proxy Buyers List", "Send Buy Order"):
            self.assertContains(resp, needle)


@override_settings(STORAGES=_NO_MANIFEST_STORAGES)
class QAPageTests(TestCase):
    """/qa/ renders live from howto_qa.md — same source the chatbot uses."""

    def test_parser_skips_preamble_and_keeps_multiline_answers(self):
        md = (
            "# Title\nEditor note (should be dropped)\n---\n"
            "## Getting started\n"
            "Q: What is it?\n"
            "A: A platform that:\n"
            "1). does one thing\n"
            "2). does another with **bold** and a [link](/contact/)\n"
            "# Buyers\n"
            "## Sub\n"
            "Q: Second?\nA: Yes.\n"
        )
        secs = _parse_qa(md)
        titles = [(s["title"], s["level"]) for s in secs]
        assert ("Getting started", 2) in titles
        assert ("Buyers", 1) in titles
        assert "Editor note" not in str(titles)
        q, a = secs[0]["items"][0]
        assert q == "What is it?"
        assert "does one thing" in a and "does another" in a  # continuation kept
        assert "<strong>bold</strong>" in a and 'href="/contact/"' in a

    def test_page_renders(self):
        resp = self.client.get(reverse("pages:qa"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "pages/qa.html")
        self.assertContains(resp, "How")
        self.assertNotContains(resp, "chatbot knowledge base")  # preamble stays hidden
