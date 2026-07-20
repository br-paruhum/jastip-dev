import json
import logging
import time
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import requests
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.blog.models import Post
from apps.notifications.services import send_email
from apps.trips.constants import OfferStatus, OPEN_PLAN_STATUSES, Status
from apps.trips.models import Order, ProxyBuyer, TravelPlan

from .forms import CarrierLeadForm, ContactForm
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

    # Flow-1 (proxy, FCFS one traveler) — unchanged. Carrier-First orders are
    # bound to a plan (carrier_first_plan) and only consume that plan's Spare
    # Weight on the Queuing Carrier board — they must never surface on this
    # Queuing Cargo board, so exclude them.
    for o in (
        Order.objects.filter(
            plan__isnull=True, cargo_only=False, proxy_buyer__isnull=False,
            carrier_first_plan__isnull=True,
            status__in={Status.RESPONDED} | CARRY_INFLIGHT,
        ).select_related("buyer").prefetch_related("traveler_offers")
    ):
        live = [off for off in o.traveler_offers.all()
                if off.offer_status in (OfferStatus.PENDING, OfferStatus.SELECTED)]
        o.board_weight = o.effective_weight_kg  # actual once weighed, else estimate
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
        .select_related("buyer", "carrier_first_plan").prefetch_related("traveler_offers")
    ):
        # Show the cargo's own weight (final once weighed, else declared) and keep
        # it steady once taken — the Status/Action columns already signal it's
        # covered, so decrementing to remaining capacity just reads as "0 kg".
        legs = o.confirmed_legs
        o.board_weight = sum(l.final_weight_kg for l in legs) if legs else o.bid_weight_kg
        if o.is_fully_matched or o.in_transit:
            o.board_state, o.is_locked_fcfs, o.can_offer = "covered", True, False
        else:
            # Carriers may keep offering until the bid weight is fully allocated.
            o.board_state = "partial" if o.confirmed_legs else "open"
            o.is_locked_fcfs = False
            o.can_offer = (not request.user.is_authenticated or o.buyer_id != request.user.id)
        cargo_looking.append(o)

    # Recently delivered orders linger on the board for 30 days after the admin
    # payout, shown as Received / Closed (both proxy Flow-1 and cargo Flow-2).
    # ponytail: DB bound on cleared_at (payout follows clear) keeps this cheap;
    # the exact 30-days-from-payout cut is done in Python via lingers_on_cargo_board.
    linger_cutoff = timezone.now() - timedelta(days=45)
    for o in (
        Order.objects.filter(
            plan__isnull=True, carrier_first_plan__isnull=True,
            status__in={Status.CLEAR, Status.CLOSED},
            cleared_at__gte=linger_cutoff,
        ).select_related("buyer").prefetch_related("traveler_offers")
    ):
        if not o.lingers_on_cargo_board():
            continue
        legs = o.confirmed_legs
        o.board_weight = (sum(l.final_weight_kg for l in legs)
                          if o.is_cargo and legs else o.effective_weight_kg)
        o.board_state = "received"
        o.is_locked_fcfs, o.can_offer = True, False
        cargo_looking.append(o)

    # Traveler-First cargo has no Order Deadline (the carrier is pre-chosen), so the
    # board's "Deadline" column falls back to the bound plan's travel date.
    for o in cargo_looking:
        o.board_deadline = o.max_acceptable_date or (
            o.carrier_first_plan.travel_date if o.carrier_first_plan_id else None
        )
    cargo_looking.sort(key=lambda o: o.board_deadline or date.max)

    # Board 2 (Carrier-First): queued carriers with spare capacity waiting for
    # orders. Buyers "Send Order" here to put a carrier in front of their orders.
    queuing_carriers = list(
        TravelPlan.objects.filter(status__in=OPEN_PLAN_STATUSES)
        .select_related("traveler")
        # cargo_offers feeds cargo_offer_committed_kg (the Avail column); its
        # deposit_verified reads the leg transaction's payments.
        .prefetch_related(
            "buy_requests", "carrier_matches__order",
            "cargo_offers__transaction__payments",
            "cargo_offers__order",   # cargo_offer_committed_kg reads off.order (proxy/products)
        )
        .order_by("travel_date", "-created_at")
    )

    latest_posts = Post.objects.filter(status=Post.Status.PUBLISHED)[:3]
    return render(
        request,
        "pages/home.html",
        {
            "proxy_buyers": proxy_buyers,
            # Board 1 (Looking for Traveler) = proxy-sourced cargo orders.
            "cargo_looking": cargo_looking,
            # Board 2 (Queuing Carrier) = open travel plans (carrier-first).
            "queuing_carriers": queuing_carriers,
            "latest_posts": latest_posts,
        },
    )


def page_detail(request, slug):
    page = get_object_or_404(SitePage, slug=slug, is_published=True)
    # The How-To guide is a static template (single source of truth); the legacy
    # CMS SitePage of the same kind is an unlinked duplicate — send it there.
    if page.kind == SitePage.Kind.HOW_TO:
        return redirect("pages:how_to")
    faqs = FAQItem.objects.filter(is_published=True) if page.kind == SitePage.Kind.FAQ else None
    return render(request, "pages/page.html", {"page": page, "faqs": faqs})


def how_to(request):
    # Legacy /how-to/ — the guide is now split into buyer/traveler pages.
    return redirect("pages:how_to_for_buyer", permanent=True)


def how_to_for_buyer(request):
    return render(request, "pages/how_to_for_buyer.html")


def how_to_for_traveler(request):
    return render(request, "pages/how_to_for_traveler.html")


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


def become_carrier(request):
    """Opt-in landing page for travellers who fly Jakarta↔Germany regularly.
    A consented lead lands as a 'Become a Carrier' ContactMessage in the admin inbox."""
    if request.method == "POST":
        form = CarrierLeadForm(request.POST)
        if request.POST.get("website"):  # honeypot tripped — fake success, save nothing
            messages.success(request, "Thanks! We'll be in touch about carrying on your next trip.")
            return redirect("pages:become_carrier")
        if not _verify_turnstile(request):
            messages.error(request, "Verification failed. Please try again.")
        elif form.is_valid():
            cd = form.cleaned_data
            freq_label = dict(form.fields["frequency"].choices).get(cd["frequency"], cd["frequency"])
            body = (
                f"Country: {cd['country']}\n"
                f"Home city: {cd['home_city']}\n"
                f"Flies to/from Indonesia: {freq_label}\n"
            )
            if cd.get("note"):
                body += f"\n{cd['note']}"
            msg = ContactMessage.objects.create(
                name=cd["name"], email=cd["email"],
                topic=ContactMessage.Topic.CARRIER, message=body,
            )
            send_email(
                to_address=settings.ADMIN_EMAIL,
                subject=f"[Carrier lead] {msg.name} — {cd['home_city']}, {cd['country']}",
                template="contact_message",
                context={"msg": msg},
                event="carrier_lead",
                cc_admin=False,
            )
            send_email(
                to_address=msg.email,
                subject=f"Thanks for your interest in carrying with {settings.SITE_NAME}",
                template="contact_confirmation",
                context={"msg": msg, "faq_url": request.build_absolute_uri(reverse("pages:page", args=["faq"]))},
                event="carrier_lead_confirmation",
                cc_admin=False,
                reply_to=["support@proxybuying.com"],
            )
            messages.success(request, "Thanks! We'll be in touch about carrying on your next trip.")
            return redirect("pages:become_carrier")
    else:
        form = CarrierLeadForm()
    return render(request, "pages/become_carrier.html", {"form": form})


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


# --- Help chatbot -----------------------------------------------------------
# Answers only from the howto_qa.md knowledge base, stuffed into the system prompt (the
# whole file fits well inside Gemini's context — no retrieval needed).

@lru_cache(maxsize=1)
def load_howto_qa() -> str:
    """Q&A knowledge base, read once per process. Restart to pick up edits.

    Lives in the Obsidian vault so it can be edited there directly.
    """
    path = (
        Path(settings.BASE_DIR)
        / "proxybuying-obsidian" / "AI-Context" / "references" / "howto_qa.md"
    )
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("howto_qa.md missing at %s", path)
        return ""


CHAT_MSG_MAXLEN = 500


@require_POST
def chat(request):
    """Grounded help chatbot backed by Anthropic Claude. Key stays server-side."""
    if not settings.ANTHROPIC_API_KEY:
        return JsonResponse({"error": "Chat is not available right now."}, status=503)

    try:
        message = (json.loads(request.body or "{}").get("message") or "").strip()
    except (ValueError, TypeError):
        return JsonResponse({"error": "Bad request."}, status=400)

    if not message:
        return JsonResponse({"error": "Please type a question."}, status=400)
    if len(message) > CHAT_MSG_MAXLEN:
        return JsonResponse(
            {"error": f"Please keep it under {CHAT_MSG_MAXLEN} characters."}, status=400
        )

    # Throttle per session: N total questions, and N per rolling minute.
    used = request.session.get("chat_used", 0)
    if used >= settings.CHATBOT_MAX_QUESTIONS:
        return JsonResponse(
            {"error": "You've reached the question limit for this session."}, status=429
        )
    now = time.time()
    recent = [t for t in request.session.get("chat_times", []) if now - t < 60]
    if len(recent) >= settings.CHATBOT_RATE_PER_MIN:
        return JsonResponse(
            {"error": "You're sending messages too fast. Please wait a minute."},
            status=429,
        )

    system_prompt = (
        f"You are the {settings.SITE_NAME} help assistant. Answer the user's "
        "question using ONLY the Q&A knowledge base below. Be concise and friendly. "
        "Always reply in the same language the user writes in (for example, answer "
        "in Bahasa Indonesia if they ask in Indonesian), even though the knowledge "
        "base is written in English. "
        "If the answer is not in the knowledge base, say you don't have that "
        "information and suggest they use the Contact page at /contact/. Do not "
        "invent policies, prices, or steps.\n\n"
        "=== KNOWLEDGE BASE ===\n" + load_howto_qa()
    )

    payload = {
        "model": settings.CHATBOT_MODEL,
        "max_tokens": settings.CHATBOT_MAX_TOKENS,
        "temperature": 0.2,
        # cache_control caches the (large, unchanging) KB system prompt so the
        # 2nd-5th questions in a session read it at ~0.1x cost instead of full price.
        "system": [{"type": "text", "text": system_prompt,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": message}],
    }
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages", json=payload, timeout=20,
            headers={
                "x-api-key": settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        reply = data["content"][0]["text"].strip()
    except (requests.RequestException, KeyError, IndexError, ValueError):
        logger.exception("Anthropic chat request failed")
        return JsonResponse(
            {"error": "Sorry, I couldn't answer that right now. Please try again."},
            status=502,
        )

    # Count only successful answers against the limits.
    request.session["chat_used"] = used + 1
    request.session["chat_times"] = recent + [now]
    request.session.modified = True
    return JsonResponse({"reply": reply, "remaining": settings.CHATBOT_MAX_QUESTIONS - used - 1})
