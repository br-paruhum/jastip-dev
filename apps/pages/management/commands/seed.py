"""Seed baseline content: site config, static pages, FAQ, admin user, demo data.

Idempotent — safe to run repeatedly.  Usage: python manage.py seed
"""

import datetime
import os
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import User
from apps.blog.models import Post
from apps.pages.models import FAQItem, SitePage
from apps.trips.constants import Currency, Status
from apps.trips.models import TravelPlan

# Rich "How It Works" infographic body — single source of truth, also rendered
# live at /how-to/.  Kept in its own file so it can be edited without bloating
# this command and so re-seeding never reverts the page to old placeholder text.
HOW_TO_BODY = (Path(__file__).resolve().parents[2] / "how_to_body.html").read_text(encoding="utf-8")

PAGES = [
    ("how-to", SitePage.Kind.HOW_TO, "How It Works", HOW_TO_BODY),
    ("faq", SitePage.Kind.FAQ, "Frequently Asked Questions", "<p>Common questions about using Jastip.me.</p>"),
    ("privacy-policy", SitePage.Kind.PRIVACY, "Privacy Policy", """
<p>We respect your privacy. Your name and phone number are never shown publicly — they are shared
only with the counterparty of a transaction and the admin. We store the minimum data needed to
operate the service and never sell your data.</p>"""),
    ("terms-conditions", SitePage.Kind.TERMS, "Terms & Conditions", """
<p>By using Jastip.me you agree to act in good faith. Jastip.me is a platform that facilitates
proxy purchasing and holds funds in escrow for a 2.5% fee. All correspondence between travelers and
buyers is conducted through email with a copy to admin.</p>"""),
]

FAQS = [
    ("How much does Jastip.me charge?", "A flat 2.5% commission on the deposit amount. The final balance is released without any extra fee."),
    ("Are my name and phone number public?", "No. They are shared only with your transaction counterparty and the admin."),
    ("How do payments work?", "Buyers transfer funds to the admin account. Admin verifies each transfer before advancing the transaction, keeping both sides safe."),
    ("What if the traveler rejects my request?", "The travel plan reopens and you can request from any other open trip. No funds change hands until a request is accepted and paid."),
]


class Command(BaseCommand):
    help = "Seed baseline site content and demo data."

    def handle(self, *args, **options):
        # Site
        site = Site.objects.get(pk=settings.SITE_ID)
        site.domain = settings.SITE_DOMAIN
        site.name = settings.SITE_NAME
        site.save()
        self.stdout.write(self.style.SUCCESS(f"Site set to {site.domain}"))

        # Static pages
        for slug, kind, title, body in PAGES:
            SitePage.objects.update_or_create(
                slug=slug, defaults={"kind": kind, "title": title, "body": body, "is_published": True}
            )
        self.stdout.write(self.style.SUCCESS(f"{len(PAGES)} site pages ready"))

        # FAQ
        for i, (q, a) in enumerate(FAQS):
            FAQItem.objects.update_or_create(question=q, defaults={"answer": a, "order": i})
        self.stdout.write(self.style.SUCCESS(f"{len(FAQS)} FAQ items ready"))

        # Admin user
        admin_email = settings.ADMIN_EMAIL
        if not User.objects.filter(email=admin_email).exists():
            User.objects.create_superuser(
                email=admin_email, password=os.getenv("ADMIN_PASSWORD", "ChangeMe!2026"),
                full_name="Jastip Admin", phone_verified=True,
            )
            self.stdout.write(self.style.WARNING(
                f"Created superuser {admin_email} (password from ADMIN_PASSWORD env or 'ChangeMe!2026')"
            ))
        else:
            self.stdout.write("Admin user already exists")

        # Google OAuth social app from env
        self._sync_google(site)

        # Demo travelers + plans
        self._demo_plans()

        # Demo blog post
        if not Post.objects.exists():
            Post.objects.create(
                title="5 things to know before you jastip",
                excerpt="A quick guide to safe, smooth proxy purchasing for first-time travelers and buyers.",
                body="Proxy purchasing is all about trust and clear communication. Here are five tips...",
                status=Post.Status.PUBLISHED, published_at=timezone.now(),
            )
            self.stdout.write(self.style.SUCCESS("Demo blog post created"))

        self.stdout.write(self.style.SUCCESS("Seed complete."))

    def _sync_google(self, site):
        cid = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
        secret = os.getenv("GOOGLE_OAUTH_SECRET", "")
        if not (cid and secret):
            self.stdout.write("Google OAuth: no env creds, skipping (fill GOOGLE_OAUTH_* in .env).")
            return
        from allauth.socialaccount.models import SocialApp
        app, _ = SocialApp.objects.update_or_create(
            provider="google", defaults={"name": "Google", "client_id": cid, "secret": secret}
        )
        app.sites.add(site)
        self.stdout.write(self.style.SUCCESS("Google OAuth social app synced from env"))

    def _demo_plans(self):
        if TravelPlan.objects.exists():
            return
        traveler, created = User.objects.get_or_create(
            email="traveler.demo@jastip.me",
            defaults={"full_name": "Demo Traveler", "phone_country_code": "+62",
                      "phone_number": "81200000001", "phone_verified": True},
        )
        if created:
            traveler.set_password("DemoTraveler!2026")
            traveler.save()
        today = timezone.now().date()
        samples = [
            (today + datetime.timedelta(days=10), "Tokyo", "Japan", "Jakarta", "Indonesia",
             Decimal("8"), Currency.JPY, Decimal("1500"), Decimal("10")),
            (today + datetime.timedelta(days=18), "Singapore", "Singapore", "Surabaya", "Indonesia",
             Decimal("5"), Currency.SGD, Decimal("12"), Decimal("8")),
            (today + datetime.timedelta(days=25), "Seoul", "South Korea", "Bandung", "Indonesia",
             Decimal("12"), Currency.KRW, Decimal("9000"), Decimal("12")),
        ]
        for d, fc, fco, tc, tco, w, cur, cpk, m in samples:
            TravelPlan.objects.create(
                traveler=traveler, travel_date=d, from_city=fc, from_country=fco,
                to_city=tc, to_country=tco, available_weight_kg=w,
                shipment_currency=cur, shipment_cost_per_kg=cpk, margin_percent=m,
                status=Status.NEW,
            )
        self.stdout.write(self.style.SUCCESS("3 demo travel plans created"))
