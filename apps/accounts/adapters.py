from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import reverse


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
    email_base.html with the ProxyBuying logo header.
    """

    def render_mail(self, template_prefix, email, context, headers=None):
        # email_base.html header references {{ logo_url }}; allauth doesn't pass
        # it, so inject the absolute logo (and site) URL into every account email.
        context.setdefault("logo_url", _static_url("img/logo-email.png"))
        context.setdefault("site_url", _site_url("/"))
        return super().render_mail(template_prefix, email, context, headers=headers)

    def send_mail(self, template_prefix, email, context):
        # Send confirmation / password-reset emails off the request cycle so a
        # slow SMTP handshake doesn't make signup or reset hang.
        from apps.notifications.services import deliver_in_background

        msg = self.render_mail(template_prefix, email, context)
        deliver_in_background(msg.send)

    def get_email_confirmation_url(self, request, emailconfirmation):
        return super().get_email_confirmation_url(request, emailconfirmation)

    def post_login(self, request, user, **kwargs):
        # Route every login (password, signup, social, login-by-code, MFA — they
        # all funnel through this hook) through the Traveler/Buyer interstitial
        # first, stashing the destination allauth would otherwise have used.
        response = super().post_login(request, user, **kwargs)
        if isinstance(response, HttpResponseRedirect):
            # A superuser manages disbursements, not orders — skip the interstitial
            # and land them on the "Pending Disbursement" tab (as a buyer, so the
            # dashboard role resolves cleanly). Honour any explicit ?next deep-link.
            if user.is_superuser:
                request.session["role"] = "buyer"
                if user.last_role_choice != "buyer":
                    user.last_role_choice = "buyer"
                    user.save(update_fields=["last_role_choice"])
                profile_url = reverse("accounts:profile")
                if response.url in (profile_url, "/"):
                    return HttpResponseRedirect(profile_url + "#pending-disbursements")
                return response
            # A Proxy Buyer always operates as a Buyer, so skip the interstitial
            # for them and land on their "My Proxy Buying" tab as a buyer.
            if user.is_proxy_buyer:
                request.session["role"] = "buyer"
                if user.last_role_choice != "buyer":
                    user.last_role_choice = "buyer"
                    user.save(update_fields=["last_role_choice"])
                # Only override the default dashboard landing; honour any explicit
                # deep-link destination allauth resolved (e.g. ?next=).
                profile_url = reverse("accounts:profile")
                if response.url in (profile_url, "/"):
                    return HttpResponseRedirect(profile_url + "#proxy-orders")
                return response
            request.session["post_role_next"] = response.url
            return HttpResponseRedirect(reverse("accounts:choose_role"))
        return response
