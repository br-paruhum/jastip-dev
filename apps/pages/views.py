import logging

import requests
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.blog.models import Post
from apps.notifications.services import send_email
from apps.trips.constants import BUYER_FIRST_TERMINAL_STATUSES, OfferStatus, Status
from apps.trips.models import BuyRequest, TravelPlan

from .forms import ContactForm
from .models import ContactMessage, FAQItem, SitePage, SiteSettings

logger = logging.getLogger(__name__)


def order_first(request):
    from apps.trips.models import TravelerOffer

    orders = list(
        BuyRequest.objects.filter(plan__isnull=True)
        .exclude(status__in=BUYER_FIRST_TERMINAL_STATUSES)
        .select_related("buyer")
        .prefetch_related("traveler_offers")
        .order_by("max_acceptable_date")
    )
    pending_lookup = {}
    if request.user.is_authenticated:
        pending_lookup = {
            off.order_id: off.id
            for off in TravelerOffer.objects.filter(
                order__in=orders, traveler=request.user, offer_status=OfferStatus.PENDING
            )
        }
    open_orders, transit_orders, closed_orders = [], [], []
    for order in orders:
        order.my_pending_offer_id = pending_lookup.get(order.id)
        {"open": open_orders, "transit": transit_orders, "closed": closed_orders}[order.home_section].append(order)
    return render(
        request,
        "pages/order_first.html",
        {"open_orders": open_orders, "transit_orders": transit_orders, "closed_orders": closed_orders},
    )


def home(request):
    open_plans = (
        TravelPlan.objects.exclude(status=Status.CLOSED)
        .select_related("traveler")
        .prefetch_related("buy_requests")[:30]
    )
    closed_plans = (
        TravelPlan.objects.filter(status=Status.CLOSED)
        .select_related("traveler")
        .prefetch_related("buy_requests")[:20]
    )
    latest_posts = Post.objects.filter(status=Post.Status.PUBLISHED)[:3]
    # Pass as a plain Decimal so {% if remaining >= MIN_REMAINING_WEIGHT_KG %} works
    # (SimpleLazyObject doesn't proxy __ge__/__le__, causing silent False comparisons).
    min_remaining = SiteSettings.load().min_remaining_weight_kg
    return render(
        request,
        "pages/home.html",
        {
            "open_plans": open_plans,
            "closed_plans": closed_plans,
            "latest_posts": latest_posts,
            "MIN_REMAINING_WEIGHT_KG": min_remaining,
        },
    )


def page_detail(request, slug):
    page = get_object_or_404(SitePage, slug=slug, is_published=True)
    faqs = FAQItem.objects.filter(is_published=True) if page.kind == SitePage.Kind.FAQ else None
    return render(request, "pages/page.html", {"page": page, "faqs": faqs})


def how_to(request):
    page = SitePage.objects.filter(kind=SitePage.Kind.HOW_TO, is_published=True).first()
    return render(request, "pages/page.html", {"page": page, "faqs": None})


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
            messages.success(request, "Thanks — your message has been sent. We'll get back to you soon.")
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
            messages.success(request, "Thanks — your message has been sent. We'll get back to you soon.")
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
