from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings


def _site_url(path: str = "") -> str:
    domain = getattr(settings, "SITE_DOMAIN", "localhost:8019")
    scheme = "http" if settings.DEBUG else "https"
    return f"{scheme}://{domain}{path}"


def _static_url(path: str) -> str:
    """Absolute URL for a static asset using the plain (non-hashed) path so email
    rendering never depends on the staticfiles manifest being current."""
    base = settings.STATIC_URL if settings.STATIC_URL.startswith("/") else "/" + settings.STATIC_URL
    return _site_url(base + path)


class AccountAdapter(DefaultAccountAdapter):
    """Sends HTML account emails (confirmation, password reset) that extend
    email_base.html with the Jastip.me logo header.
    """

    def render_mail(self, template_prefix, email, context, headers=None):
        # email_base.html header references {{ logo_url }}; allauth doesn't pass
        # it, so inject the absolute logo (and site) URL into every account email.
        context.setdefault("logo_url", _static_url("img/logo-email.png"))
        context.setdefault("site_url", _site_url("/"))
        return super().render_mail(template_prefix, email, context, headers=headers)

    def get_email_confirmation_url(self, request, emailconfirmation):
        return super().get_email_confirmation_url(request, emailconfirmation)
