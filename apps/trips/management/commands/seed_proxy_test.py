"""Seed proxy buyers + test logins for exercising the new Proxy Buyer model.

Idempotent: safe to run repeatedly (upserts by email / by proxy name+country).
Creates verified, password-set accounts so you can log in immediately — unlike
the admin flow (which leaves the proxy password unusable until 'Forgot password').

Usage:
    python manage.py seed_proxy_test
    python manage.py seed_proxy_test --password test1234   # override shared pw

All seeded accounts share one password (default "test1234"). Proxy buyers are at
the ORIGIN country (where they source goods); the buyer/traveler are generic.
"""

from django.core.management.base import BaseCommand
from django.db import transaction


# (name, country, city, margin_range, email, whatsapp)
PROXY_BUYERS = [
    ("Hiro Tanaka", "Japan",     "Tokyo",     "15-25%", "proxy.japan@proxybuying.com", "+817012345678"),
    ("Mei Tan",     "Singapore", "Singapore", "10-20%", "proxy.sg@proxybuying.com",    "+6598765432"),
    ("Agung",       "Indonesia", "Jakarta",   "12-22%", "paruhum@placeq.com",          "+628111222333"),
]

# (email, full_name, role)
EXTRA_ACCOUNTS = [
    ("demobuyer@proxybuying.com",    "Demo Buyer",    "buyer"),
    ("demotraveler@proxybuying.com", "Demo Traveler", "traveler"),
]


class Command(BaseCommand):
    help = "Seed proxy buyers and test buyer/traveler/proxy logins (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="test1234",
                            help="Shared password for all seeded accounts (default: test1234).")

    @transaction.atomic
    def handle(self, *args, **opts):
        from allauth.account.models import EmailAddress

        from apps.accounts.models import User
        from apps.trips.models import ProxyBuyer

        pw = opts["password"]

        def ensure_user(email, full_name, role):
            """Create or update a verified, password-set account."""
            user, created = User.objects.get_or_create(
                email=email,
                defaults={"full_name": full_name, "last_role_choice": role,
                          "phone_verified": True},
            )
            if not created:
                user.full_name = user.full_name or full_name
                if not user.last_role_choice:
                    user.last_role_choice = role
                user.phone_verified = True
            user.set_password(pw)
            user.save()
            EmailAddress.objects.update_or_create(
                user=user, email=user.email,
                defaults={"verified": True, "primary": True},
            )
            self.stdout.write(f"  {'created' if created else 'updated'} login  {email}  ({role})")
            return user

        self.stdout.write(self.style.MIGRATE_HEADING("Proxy buyers + linked logins:"))
        for seq, (name, country, city, margin, email, wa) in enumerate(PROXY_BUYERS):
            user = ensure_user(email, name, "buyer")
            ProxyBuyer.objects.update_or_create(
                name=name, country=country,
                defaults={"city": city, "margin_range": margin, "email": email,
                          "whatsapp": wa, "user": user, "is_active": True, "sequence": seq},
            )
            self.stdout.write(f"    proxy buyer  {name} — {country} / {city}")

        self.stdout.write(self.style.MIGRATE_HEADING("Test buyer / traveler logins:"))
        for email, full_name, role in EXTRA_ACCOUNTS:
            ensure_user(email, full_name, role)

        self.stdout.write(self.style.SUCCESS(
            f"\nSeed complete. All accounts use password: {pw!r}\n"
            "Proxy buyers now appear on the home Proxy Buyers board (with City)."
        ))
