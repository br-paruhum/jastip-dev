from django.conf import settings
from django.utils.functional import SimpleLazyObject


def site_globals(request):
    """Values available in every template."""

    def _promos():
        from .models import Promo

        return list(Promo.objects.filter(is_active=True))

    return {
        "SITE_NAME": getattr(settings, "SITE_NAME", "Jastip.me"),
        "SITE_DOMAIN": getattr(settings, "SITE_DOMAIN", ""),
        "ADSENSE_CLIENT": getattr(settings, "ADSENSE_CLIENT", ""),
        "COMMISSION_PERCENT": getattr(settings, "PLATFORM_COMMISSION_PERCENT", 2.5),
        "BANK": getattr(settings, "BANK_DETAILS", {}),
        "PAYMENT_DEADLINE_HOURS": getattr(settings, "PAYMENT_DEADLINE_HOURS", 24),
        # Lazy: the DB query only runs if a template actually renders the sidebar.
        "sidebar_promos": SimpleLazyObject(_promos),
    }
