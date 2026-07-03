import logging
from datetime import date

import requests
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.blog.models import Post
from apps.notifications.services import send_email
from apps.trips.constants import OfferStatus, Status
from apps.trips.models import Order, ProxyBuyer

from .forms import ContactForm
from .models import ContactMessage, FAQItem, SitePage

logger = logging.getLogger(__name__)


def home(request):
    proxy_buyers = list(ProxyBuyer.objects.filter(is_active=True))

    # "Looking for Carrier" board = both flavours of cargo that needs a traveler:
    #   Flow-1: proxy-sourced orders whose estimate is sent (FCFS — one traveler).
    #   Flow-2: buyer-owned cargo orders (one buyer → many carriers, partial legs).
    # We keep each on the board through its active carry lifecycle so other
    # travelers can see progress (Open → Pending/Partial → Covered), then it drops
    # off once delivered/cleared.
    CARRY_INFLIGHT = {
        Status.ACCEPTED, Status.DEPOSIT_PAID, Status.ITEMS_PURCHASED,
        Status.PACKAGE_RECEIVED, Status.PACKAGE_ARRIVED,
    }
    CARGO_DONE = {
        Status.CLOSED, Status.CANCELLED, Status.DROPOFF_MISSED,
        Status.CLEAR, Status.READY_FOR_PICKUP,
    }
    cargo_looking = []

    # Flow-1 (proxy, FCFS one traveler) — unchanged.
    for o in (
        Order.objects.filter(
            plan__isnull=True, cargo_only=False, proxy_buyer__isnull=False,
            status__in={Status.RESPONDED} | CARRY_INFLIGHT,
        ).select_related("buyer").prefetch_related("traveler_offers")
    ):
        live = [off for off in o.traveler_offers.all()
                if off.offer_status in (OfferStatus.PENDING, OfferStatus.SELECTED)]
        o.board_weight = o.estimated_weight_kg
        if o.status in CARRY_INFLIGHT:
            o.board_state, o.is_locked_fcfs, o.can_offer = "covered", True, False
        else:  # RESPONDED — stay open so more carriers can offer until the
               # buyer accepts one (then status leaves RESPONDED and it locks).
            o.board_state = "pending" if live else "open"
            o.is_locked_fcfs = False
            o.can_offer = (
                not request.user.is_authenticated or o.buyer_id != request.user.id
            )
        cargo_looking.append(o)

    # Flow-2 (buyer-owned cargo, one→many) — partial fulfillment across legs.
    for o in (
        Order.objects.filter(plan__isnull=True, cargo_only=True)
        .exclude(status__in=CARGO_DONE)
        .select_related("buyer").prefetch_related("traveler_offers")
    ):
        o.board_weight = o.remaining_bid_weight_kg  # capacity still needed
        if o.is_fully_matched or o.in_transit:
            o.board_state, o.is_locked_fcfs, o.can_offer = "covered", True, False
        else:
            # Carriers may keep offering until the bid weight is fully allocated.
            o.board_state = "partial" if o.confirmed_legs else "open"
            o.is_locked_fcfs = False
            o.can_offer = (not request.user.is_authenticated or o.buyer_id != request.user.id)
        cargo_looking.append(o)

    cargo_looking.sort(key=lambda o: o.max_acceptable_date or date.max)

    latest_posts = Post.objects.filter(status=Post.Status.PUBLISHED)[:3]
    return render(
        request,
        "pages/home.html",
        {
            "proxy_buyers": proxy_buyers,
            # Board 1 (Looking for Traveler) = proxy-sourced cargo orders.
            "cargo_looking": cargo_looking,
            "latest_posts": latest_posts,
        },
    )


def page_detail(request, slug):
    page = get_object_or_404(SitePage, slug=slug, is_published=True)
    faqs = FAQItem.objects.filter(is_published=True) if page.kind == SitePage.Kind.FAQ else None
    return render(request, "pages/page.html", {"page": page, "faqs": faqs})


def how_to(request):
    # Static How-To page (Task 13) — replaces the former CMS-driven SitePage so
    # the guide is a single source of truth in the template, not the database.
    return render(request, "pages/how_to.html")


def _verify_turnstile(request):
    """Validate the Cloudflare Turnstile token. Returns True when verification
    isn't configured (keys blank) so the form keeps working until keys arrive."""
    secret = getattr(settings, "TURNSTILE_SECRET_KEY", "")
    if not secret:
        return True
    token = request.POST.get("cf-turnstile-response", "")
    if not token:
        return False
    try:
        resp = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": secret,
                "response": token,
                "remoteip": request.META.get("REMOTE_ADDR", ""),
            },
            timeout=5,
        )
        return bool(resp.json().get("success"))
    except requests.RequestException:
        # Cloudflare unreachable: don't block a legit visitor (honeypot still applies).
        logger.warning("Turnstile verification request failed; allowing submission.")
        return True


def contact(request):
    if request.method == "POST":
        form = ContactForm(request.POST)
        # Honeypot tripped -> pretend success, save nothing (don't tip off bots).
        if request.POST.get("website"):
            messages.success(request, "Message sent successfully! Thanks for reaching out. We will look over your request and get back to you within 24-48 hours.")
            return redirect("pages:contact")
        if not _verify_turnstile(request):
            messages.error(request, "Verification failed. Please try again.")
        elif form.is_valid():
            msg = form.save()
            send_email(
                to_address=settings.ADMIN_EMAIL,
                subject=f"[Contact] {msg.get_topic_display()} — {msg.name}",
                template="contact_message",
                context={"msg": msg},
                event="contact",
                cc_admin=False,
            )
            send_email(
                to_address=msg.email,
                subject="Thank you for contacting ProxyBuying.",
                template="contact_confirmation",
                context={"msg": msg, "faq_url": request.build_absolute_uri(reverse("pages:page", args=["faq"]))},
                event="contact_confirmation",
                cc_admin=False,
                reply_to=["support@proxybuying.com"],
            )
            messages.success(request, "Message sent successfully! Thanks for reaching out. We will look over your request and get back to you within 24-48 hours.")
            return redirect("pages:contact")
    else:
        topic = request.GET.get("topic")
        initial = {"topic": topic} if topic in ContactMessage.Topic.values else {}
        form = ContactForm(initial=initial)
    return render(request, "pages/contact.html", {"form": form})


@require_GET
def robots_txt(request):
    # Staging/dev (ALLOW_INDEXING off) block all crawling so they never compete
    # with production for the same content.
    if not getattr(settings, "ALLOW_INDEXING", False):
        return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /u/",
        f"Sitemap: {request.scheme}://{request.get_host()}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


@require_GET
def ads_txt(request):
    """Google AdSense ads.txt. Empty (204) until ADSENSE_CLIENT is configured."""
    client = settings.ADSENSE_CLIENT
    if not client:
        return HttpResponse(status=204)
    pub = client.replace("ca-", "")
    return HttpResponse(
        f"google.com, {pub}, DIRECT, f08c47fec0942fa0\n", content_type="text/plain"
    )
