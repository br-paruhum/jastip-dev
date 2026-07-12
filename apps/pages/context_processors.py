from django.conf import settings
from django.utils.functional import SimpleLazyObject


def site_globals(request):
    """Values available in every template."""

    def _promos():
        from .models import Promo

        return list(Promo.objects.filter(is_active=True))

    def _payment_term():
        from .models import SiteSettings
        return SiteSettings.load().payment_term

    def _payment_term_carrier():
        from .models import SiteSettings
        return SiteSettings.load().payment_term_carrier

    return {
        "SITE_NAME": getattr(settings, "SITE_NAME", "ProxyBuying"),
        "SITE_DOMAIN": getattr(settings, "SITE_DOMAIN", ""),
        "ADSENSE_CLIENT": getattr(settings, "ADSENSE_CLIENT", ""),
        "CHATBOT_ENABLED": bool(getattr(settings, "ANTHROPIC_API_KEY", "")),
        "TURNSTILE_SITE_KEY": getattr(settings, "TURNSTILE_SITE_KEY", ""),
        "COMMISSION_PERCENT": getattr(settings, "PLATFORM_COMMISSION_PERCENT", 2.5),
        "BANK": getattr(settings, "BANK_DETAILS", {}),
        "PAYMENT_DEADLINE_HOURS": getattr(settings, "PAYMENT_DEADLINE_HOURS", 24),
        # Lazy: the DB query only runs if a template actually renders the sidebar.
        "sidebar_promos": SimpleLazyObject(_promos),
        # Site-wide payment terms (admin-editable; carrier-only plans use a separate one).
        "PAYMENT_TERM": SimpleLazyObject(_payment_term),
        "PAYMENT_TERM_CARRIER": SimpleLazyObject(_payment_term_carrier),
    }
