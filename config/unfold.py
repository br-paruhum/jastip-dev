"""django-unfold admin theme configuration.

Palette sampled from anthropic.com — clay / "book cloth" accent (#CC785C)
on an ivory base, with slate neutrals.
"""

from django.templatetags.static import static
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

UNFOLD = {
    "SITE_TITLE": "Jastip.me Admin",
    "SITE_HEADER": "Jastip.me",
    "SITE_SUBHEADER": "Proxy purchasing platform",
    "SITE_URL": "/",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "ENVIRONMENT": "config.unfold_callbacks.environment_callback",
    "BORDER_RADIUS": "8px",
    "COLORS": {
        "base": {
            "50": "250 249 245",
            "100": "245 243 236",
            "200": "235 231 219",
            "300": "214 207 189",
            "400": "168 162 148",
            "500": "115 110 100",
            "600": "82 78 71",
            "700": "61 58 53",
            "800": "40 38 35",
            "900": "26 25 23",
            "950": "16 15 14",
        },
        # Clay / terracotta accent (#CC785C family)
        "primary": {
            "50": "251 242 238",
            "100": "246 228 219",
            "200": "237 201 184",
            "300": "227 173 149",
            "400": "215 119 87",
            "500": "204 120 92",
            "600": "184 96 70",
            "700": "153 74 53",
            "800": "122 60 44",
            "900": "100 51 39",
            "950": "54 26 19",
        },
        "font": {
            "subtle-light": "115 110 100",
            "subtle-dark": "168 162 148",
            "default-light": "61 58 53",
            "default-dark": "245 243 236",
            "important-light": "26 25 23",
            "important-dark": "250 249 245",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": _("Overview"),
                "separator": False,
                "items": [
                    {
                        "title": _("Dashboard"),
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                    },
                ],
            },
            {
                "title": _("Marketplace"),
                "separator": True,
                "items": [
                    {
                        "title": _("Travel plans"),
                        "icon": "flight_takeoff",
                        "link": reverse_lazy("admin:trips_travelplan_changelist"),
                    },
                    {
                        "title": _("Buy requests"),
                        "icon": "shopping_bag",
                        "link": reverse_lazy("admin:trips_buyrequest_changelist"),
                    },
                    {
                        "title": _("Transactions"),
                        "icon": "payments",
                        "link": reverse_lazy("admin:trips_transaction_changelist"),
                    },
                ],
            },
            {
                "title": _("Content"),
                "separator": True,
                "items": [
                    {
                        "title": _("Blog posts"),
                        "icon": "article",
                        "link": reverse_lazy("admin:blog_post_changelist"),
                    },
                    {
                        "title": _("Site pages"),
                        "icon": "description",
                        "link": reverse_lazy("admin:pages_sitepage_changelist"),
                    },
                ],
            },
            {
                "title": _("People"),
                "separator": True,
                "items": [
                    {
                        "title": _("Users"),
                        "icon": "person",
                        "link": reverse_lazy("admin:accounts_user_changelist"),
                    },
                ],
            },
        ],
    },
}
