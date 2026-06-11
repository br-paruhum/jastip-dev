from django.db import models
from django.urls import reverse


class SitePage(models.Model):
    """Editable static pages: How To, FAQ, Privacy Policy, Terms of Use."""

    class Kind(models.TextChoices):
        HOW_TO = "how-to", "How To"
        FAQ = "faq", "FAQ"
        PRIVACY = "privacy", "Privacy Policy"
        TERMS = "terms", "Terms of Use"
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
        max_length=24, blank=True, help_text="Small label, e.g. 'Sponsored', 'Tip', 'For travelers'."
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
