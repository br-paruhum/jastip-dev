from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from apps.blog.models import Post
from apps.trips.constants import Status
from apps.trips.models import TravelPlan

from .models import FAQItem, SitePage


def home(request):
    open_plans = (
        TravelPlan.objects.exclude(status=Status.CLOSED)
        .select_related("traveler")
        .prefetch_related("buy_requests")[:30]
    )
    closed_plans = (
        TravelPlan.objects.filter(status=Status.CLOSED).select_related("traveler")[:20]
    )
    latest_posts = Post.objects.filter(status=Post.Status.PUBLISHED)[:3]
    return render(
        request,
        "pages/home.html",
        {
            "open_plans": open_plans,
            "closed_plans": closed_plans,
            "latest_posts": latest_posts,
        },
    )


def page_detail(request, slug):
    page = get_object_or_404(SitePage, slug=slug, is_published=True)
    faqs = FAQItem.objects.filter(is_published=True) if page.kind == SitePage.Kind.FAQ else None
    return render(request, "pages/page.html", {"page": page, "faqs": faqs})


def how_to(request):
    page = SitePage.objects.filter(kind=SitePage.Kind.HOW_TO, is_published=True).first()
    return render(request, "pages/page.html", {"page": page, "faqs": None})


@require_GET
def robots_txt(request):
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
