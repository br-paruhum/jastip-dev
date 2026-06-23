from decimal import Decimal

from django.db import models
from django.urls import reverse

from apps.trips.constants import DEFAULT_PAYMENT_TERM, DEFAULT_PAYMENT_TERM_CARRIER


class SiteSettings(models.Model):
    """Site-wide settings editable in admin. A single (singleton) row.

    Use SiteSettings.load() to read it from anywhere; it is exposed to every
    template as PAYMENT_TERM via apps.pages.context_processors.site_globals.
    """

    payment_term = models.TextField(
        default=DEFAULT_PAYMENT_TERM,
        help_text=(
            "Shown as the Payment Term on Proxy Buyer trip detail pages. "
            "HTML allowed, e.g. <ul><li>first point</li><li>second point</li></ul>."
        ),
    )
    payment_term_carrier = models.TextField(
        default=DEFAULT_PAYMENT_TERM_CARRIER,
        help_text=(
            "Shown as the Payment Term on Carrier Only trip detail pages. "
            "HTML allowed, e.g. <ul><li>first point</li><li>second point</li></ul>."
        ),
    )
    min_remaining_weight_kg = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.99"),
        help_text=(
            "Plans with remaining capacity below this threshold show as Full on the home page "
            "and block new orders. Default 0.99 kg."
        ),
    )
    platform_fee_min_idr = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("50000.00"),
        verbose_name="Minimum platform fee (IDR)",
        help_text=(
            "The 2.5% platform fee is floored to this amount (in IDR). Applied to each "
            "carrier payout and to the proxy buyer's final disbursement. Default 50,000."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self):
        return "Site Settings"

    def save(self, *args, **kwargs):
        # Force a single row so there is only ever one settings object.
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SitePage(models.Model):
    """Editable static pages: How To, FAQ, Privacy Policy, Terms & Conditions."""

    class Kind(models.TextChoices):
        HOW_TO = "how-to", "How To"
        FAQ = "faq", "FAQ"
        PRIVACY = "privacy", "Privacy Policy"
        TERMS = "terms", "Terms & Conditions"
        GENERIC = "generic", "Generic"

    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.GENERIC)
    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    body = models.TextField(help_text="HTML allowed.")
    meta_description = models.CharField(max_length=200, blank=True)
    is_published = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("pages:page", args=[self.slug])


class Promo(models.Model):
    """A sidebar promo card. Fills the ad sidebar with house ads or sponsor
    placements until AdSense is approved; once ADSENSE_CLIENT is set the sidebar
    renders AdSense units instead. Editable in admin so cards can be swapped in
    and out without touching templates."""

    title = models.CharField(max_length=120)
    body = models.CharField(max_length=240, blank=True)
    image = models.ImageField(upload_to="promos/", blank=True, null=True)
    # Path or URL — allows internal links ("/how-to/") as well as external ones.
    url = models.CharField(max_length=300, blank=True)
    cta_label = models.CharField(max_length=40, blank=True, default="Learn more")
    badge = models.CharField(
        max_length=24, blank=True, help_text="Small label, e.g. 'Sponsored', 'Tip', 'For carriers'."
    )
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


class ContactMessage(models.Model):
    """An enquiry submitted through the public /contact/ page."""

    class Topic(models.TextChoices):
        ADVERTISE = "advertise", "Advertising"
        SUPPORT = "support", "Support"
        FEEDBACK = "feedback", "Feedback"
        OTHER = "other", "Other"

    name = models.CharField(max_length=120)
    email = models.EmailField()
    topic = models.CharField(max_length=20, choices=Topic.choices, default=Topic.OTHER)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    handled = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_topic_display()} — {self.name}"


class FAQItem(models.Model):
    question = models.CharField(max_length=240)
    answer = models.TextField()
    order = models.PositiveSmallIntegerField(default=0)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.question
