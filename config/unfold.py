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
        # Single source of truth: the curated left menu below. The duplicate
        # auto-generated "all applications" list is turned off so there is only
        # one menu to scan. Groups mirror the Django apps 1:1 — the headings
        # match the app names shown in the breadcrumb (Home > <App> > <Model>),
        # so the sidebar and breadcrumb stay consistent. Sub-items are prefixed
        # with "- " to read as a sub-menu under each app heading.
        "show_all_applications": False,
        "navigation": [
            {
                "title": _("Overview"),
                "separator": False,
                "items": [
                    {
                        "title": _("- Dashboard"),
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                    },
                ],
            },
            {
                "title": _("Trips"),
                "separator": True,
                "items": [
                    {
                        "title": _("- Travel plans"),
                        "icon": "flight_takeoff",
                        "link": reverse_lazy("admin:trips_travelplan_changelist"),
                    },
                    {
                        "title": _("- Buy requests"),
                        "icon": "shopping_bag",
                        "link": reverse_lazy("admin:trips_buyrequest_changelist"),
                    },
                    {
                        "title": _("- Transactions"),
                        "icon": "payments",
                        "link": reverse_lazy("admin:trips_transaction_changelist"),
                    },
                    {
                        "title": _("- Payments"),
                        "icon": "account_balance_wallet",
                        "link": reverse_lazy("admin:trips_payment_changelist"),
                    },
                    {
                        "title": _("- Refunds"),
                        "icon": "currency_exchange",
                        "link": reverse_lazy("admin:trips_refund_changelist"),
                    },
                    {
                        "title": _("- Messages"),
                        "icon": "forum",
                        "link": reverse_lazy("admin:trips_message_changelist"),
                    },
                ],
            },
            {
                "title": _("Pages"),
                "separator": True,
                "items": [
                    {
                        "title": _("- Site pages"),
                        "icon": "description",
                        "link": reverse_lazy("admin:pages_sitepage_changelist"),
                    },
                    {
                        "title": _("- FAQ items"),
                        "icon": "quiz",
                        "link": reverse_lazy("admin:pages_faqitem_changelist"),
                    },
                ],
            },
            {
                "title": _("Blog"),
                "separator": True,
                "items": [
                    {
                        "title": _("- Posts"),
                        "icon": "article",
                        "link": reverse_lazy("admin:blog_post_changelist"),
                    },
                ],
            },
            {
                "title": _("Accounts"),
                "separator": True,
                "items": [
                    {
                        "title": _("- Users"),
                        "icon": "person",
                        "link": reverse_lazy("admin:accounts_user_changelist"),
                    },
                    {
                        "title": _("- Email addresses"),
                        "icon": "mail",
                        "link": reverse_lazy("admin:account_emailaddress_changelist"),
                    },
                ],
            },
            {
                "title": _("Authentication and Authorization"),
                "separator": True,
                "items": [
                    {
                        "title": _("- Groups"),
                        "icon": "groups",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                    },
                ],
            },
            {
                "title": _("Social Accounts"),
                "separator": True,
                "items": [
                    {
                        "title": _("- Social accounts"),
                        "icon": "link",
                        "link": reverse_lazy("admin:socialaccount_socialaccount_changelist"),
                    },
                    {
                        "title": _("- Social applications"),
                        "icon": "apps",
                        "link": reverse_lazy("admin:socialaccount_socialapp_changelist"),
                    },
                    {
                        "title": _("- Social application tokens"),
                        "icon": "key",
                        "link": reverse_lazy("admin:socialaccount_socialtoken_changelist"),
                    },
                ],
            },
            {
                "title": _("Sites"),
                "separator": True,
                "items": [
                    {
                        "title": _("- Sites"),
                        "icon": "public",
                        "link": reverse_lazy("admin:sites_site_changelist"),
                    },
                ],
            },
            {
                "title": _("Notifications"),
                "separator": True,
                "items": [
                    {
                        "title": _("- Notification logs"),
                        "icon": "notifications",
                        "link": reverse_lazy("admin:notifications_notificationlog_changelist"),
                    },
                ],
            },
        ],
    },
}
