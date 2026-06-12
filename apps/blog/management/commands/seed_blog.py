"""Seed the 5 launch blog posts with their sketch feature images.

Idempotent: posts are keyed by slug and created only if missing, so re-running
never duplicates and never clobbers later admin edits. Feature images live in
apps/blog/seed_assets/ (git-tracked) and are attached to each post's cover_image
(copied into MEDIA on first create).

Usage:  python manage.py seed_blog
"""
import datetime
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.blog.models import Post

ASSETS = Path(__file__).resolve().parents[2] / "seed_assets"


# (slug, title, excerpt, image filename, YYYY-MM-DD published, body)
POSTS = [
    (
        "what-is-jastip",
        "What Is Jastip? Proxy Purchasing, Explained Simply",
        "“Jastip” means entrusted-buying — travelers carry items home for buyers who want them. Here’s the whole idea in plain English.",
        "01-what-is-jastip.png",
        "2026-06-12",
        """Ever wanted something that is only sold overseas — a skincare product, a pair of sneakers, a gadget — but the international shipping cost was almost as much as the item itself? Jastip.me was built for exactly that moment.

“Jastip” is short for “jasa titip”, an Indonesian phrase that means “entrusted-buying service”. The idea is simple: travelers who already have a trip planned carry items home for buyers who want them. You get your product, the traveler earns a little for the space in their luggage, and everyone wins.

Here is how it works in one breath: a traveler posts their trip — where they are going, when, and how much spare luggage weight they have. A buyer finds that trip, requests an item, and pays a deposit. The traveler buys the item abroad, brings it home, and hands it over. Jastip.me sits safely in the middle, holding the funds until both sides are happy.

Why use a platform instead of just asking a friend? Three reasons: safety, fairness, and convenience. Your money is held securely and only released when the deal is done. Prices and weight are calculated transparently. And you do not need a friend flying at the right time — there is a whole community of travelers to match with.

Jastip.me charges a small 2.5% fee on the transaction. That is it — no hidden mark-ups, no surprise charges.

Whether you are a buyer hunting for something from abroad, or a traveler who would like to turn empty luggage space into extra income, Jastip.me is the simplest way to make it happen. Welcome aboard.""",
    ),
    (
        "earn-money-travelling-abroad",
        "Studying or Working Abroad? Turn Your Trips Home into Extra Income",
        "Fly home now and then? Your spare luggage space can pay for part of the flight — without changing your plans at all.",
        "02-earn-abroad.png",
        "2026-06-10",
        """If you are studying or working overseas and fly home to Indonesia now and then, you are sitting on something valuable: spare luggage space. With Jastip.me, that space can become real income — without changing your travel plans at all.

Here is the idea. Before your next trip home, you post a Travel Plan: your route, your travel date, how many kilograms you can spare, and your rate per kilogram. Buyers back home browse trips like yours and send you requests for items they want from your city abroad. You decide which requests to accept.

Once a buyer pays their deposit, Jastip.me confirms the funds are secured, and you go shopping — often for things you might be buying anyway. You record the actual cost and weight, fly home as planned, and hand over the items. After everyone confirms, you are paid the full amount, minus a small 2.5% platform fee.

A few reasons students and young professionals love it:

You travel anyway. The trip is already happening — this just makes it pay.
You stay in control. You pick the requests, the items, and the weight you are comfortable carrying.
Your money is protected. Buyers pay a deposit up front, held safely until the handover.
No awkward chasing. Payments and reminders are handled by the platform, by email and WhatsApp.

It is a friendly way to offset the cost of a flight home, help people get products they cannot easily find, and earn from space you would otherwise leave empty. If you have a trip coming up, post it — your first order might already be waiting.""",
    ),
    (
        "how-jastip-keeps-your-money-safe",
        "How Jastip Keeps Your Money Safe: Deposit, Escrow & the 2.5% Fee",
        "You pay the platform, not a stranger — and funds are released only when you’re satisfied. Here’s the safety net explained.",
        "03-money-safe.png",
        "2026-06-08",
        """Sending money for something a stranger will buy and carry for you can feel risky. That is exactly the problem Jastip.me is designed to solve. Here is how your money stays protected from start to finish.

It starts with a deposit, not full payment. When a traveler accepts your order, you pay a deposit to the Jastip.me business account — not directly to the traveler. We verify the funds, then tell the traveler their deposit is secured so they can start purchasing. Your money is held by the platform, not handed to anyone yet.

The funds are released only when the deal is done. The traveler buys the items, brings them home, and you receive them. Once you have checked everything and marked the order as clear, the traveler is paid. If something is not right, the funds are still with Jastip.me while it gets sorted out — no one disappears with your money.

Everything is transparent. The item cost, the shipment weight, and the fees are all calculated openly and shown on your invoice. The only charge Jastip.me takes is a flat 2.5% fee on the transaction. There are no hidden mark-ups.

Your privacy is protected too. Your name and phone number are never shown publicly — they are shared only with the counterparty of your transaction and the admin, so deals stay private and on-platform.

In short: you pay the platform, not a stranger; the money is released only when you are satisfied; and every number is laid out clearly. That is the safety net that lets travelers and buyers who have never met do business with confidence.""",
    ),
    (
        "buyers-guide-products-from-abroad",
        "A Buyer’s Guide: Authentic Products from Overseas, Hassle-Free",
        "From finding the right trip to confirming delivery — the simple steps to getting an overseas product through Jastip.me.",
        "04-buyer-guide.png",
        "2026-06-05",
        """Want a product that is only sold overseas, without paying sky-high shipping or worrying about whether it is genuine? Here is how to get it through Jastip.me, step by step.

Find a trip that matches. Browse the open Travel Plans on Jastip.me. Each one shows the traveler’s route, travel date, available weight, and rate. Pick a trip coming from the city or country where your item is sold.

Send your order. Click Order, sign in, and fill in what you want — the product, roughly how much it costs, and any details that matter (size, colour, variant). The traveler gets notified by email and WhatsApp.

Wait for acceptance, then pay the deposit. The traveler reviews your request, estimates the cost and weight, and accepts. You then pay a deposit to the Jastip.me business account, held safely until the item is delivered.

Track the progress. The traveler buys your item, records the actual cost and weight, and can upload a photo. You can follow every step from your Profile page.

Receive and confirm. When the traveler arrives and the package is ready, you settle any remaining balance, collect your item, and mark the order as clear. That final confirmation releases payment to the traveler.

A few tips for a smooth purchase: be specific about the exact item and variant, keep your cost estimate realistic, and use the in-app chat for any questions so everything stays on record. Do that, and getting authentic products from abroad becomes about as easy as ordering anything else online — just with a friendly traveler doing the carrying.""",
    ),
    (
        "8-steps-of-a-jastip-transaction",
        "From Order to Doorstep: The 8 Steps of a Jastip Transaction",
        "The complete journey from placing an order to receiving your item — transparent at every step.",
        "05-eight-steps.png",
        "2026-06-02",
        """Curious what actually happens between placing an order and receiving your item on Jastip.me? Here is the whole journey, start to finish.

1. The traveler posts a Travel Plan — date, route, available weight, shipment cost, and margin.

2. The buyer clicks Order, signs in, and fills in their buying request. The traveler is notified by email and WhatsApp.

3. The traveler reviews the request, estimates the cost and weight of each item, and accepts or rejects. The buyer is notified either way.

4. If accepted, the buyer transfers the deposit to the Jastip.me business account. Once the funds are verified, the traveler is told the deposit is secured.

5. The traveler starts purchasing, records each item’s actual cost, uploads a photo (optional), and notes the actual weight. The invoice updates automatically, and the buyer can follow along from their Profile.

6. The traveler arrives at the destination city, pays any customs duty, and marks the package as arrived. The buyer is notified.

7. The buyer pays any remaining balance. Once verified, the order moves to ready for pickup.

8. The buyer receives the package, checks everything, and marks it as clear. The traveler is then paid the full amount, less the 2.5% fee — and the deal is complete.

That is the entire flow: transparent at every step, with your money held safely in the middle until both sides are happy. Whether you are buying or carrying, you always know exactly where things stand.""",
    ),
]


class Command(BaseCommand):
    help = "Seed the 5 launch blog posts with sketch feature images (idempotent)."

    def handle(self, *args, **options):
        created_n = 0
        for slug, title, excerpt, img, pub, body in POSTS:
            if Post.objects.filter(slug=slug).exists():
                self.stdout.write(f"• exists, skipped: {slug}")
                continue
            published = timezone.make_aware(
                datetime.datetime.strptime(pub, "%Y-%m-%d")
            )
            post = Post.objects.create(
                slug=slug,
                title=title,
                excerpt=excerpt,
                body=body,
                status=Post.Status.PUBLISHED,
                published_at=published,
            )
            img_path = ASSETS / img
            if img_path.exists():
                with open(img_path, "rb") as fh:
                    post.cover_image.save(img, File(fh), save=True)
            else:
                self.stdout.write(self.style.WARNING(f"  image missing: {img_path}"))
            created_n += 1
            self.stdout.write(self.style.SUCCESS(f"✓ created: {slug}"))
        self.stdout.write(self.style.SUCCESS(f"seed_blog done — {created_n} new post(s)."))
