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


class FAQItem(models.Model):
    question = models.CharField(max_length=240)
    answer = models.TextField()
    order = models.PositiveSmallIntegerField(default=0)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.question
