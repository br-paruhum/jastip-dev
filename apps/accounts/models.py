import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .managers import UserManager


class User(AbstractUser):
    """Email-based user. Names/phone are private (shown only to the
    counterparty and admin), so they live here rather than on a public profile.
    """

    username = None
    email = models.EmailField(_("email address"), unique=True)

    # Private profile fields (filled on the profile page after first login).
    full_name = models.CharField(_("full name"), max_length=120, blank=True)
    phone_country_code = models.CharField(max_length=6, blank=True, help_text="e.g. +62")
    phone_number = models.CharField(max_length=20, blank=True)
    phone_verified = models.BooleanField(default=False)
    destination_city = models.CharField(
        max_length=80, blank=True,
        help_text="The city you'll actually receive your package in — may differ from the traveler's listed (often airport) city.",
    )
    address = models.TextField(
        blank=True,
        help_text="Pickup address at your destination country, used if you choose local Pickup instead of Reshipment. Also shown on customs invoices. Fill this in now or later, once you decide.",
    )
    bank_details = models.TextField(blank=True, help_text="For invoice disbursement purpose.")

    # WhatsApp OTP verification state.
    phone_otp = models.CharField(max_length=6, blank=True)
    phone_otp_sent_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email

    @property
    def phone_e164(self) -> str:
        if self.phone_country_code and self.phone_number:
            digits = self.phone_number.lstrip("0")
            return f"{self.phone_country_code}{digits}".replace(" ", "")
        return ""

    @property
    def profile_complete(self) -> bool:
        """Gate for posting offers/bids: name + verified WhatsApp number."""
        return bool(self.full_name) and self.phone_verified

    @property
    def display_name(self) -> str:
        """Public-safe label that never leaks the real name."""
        return f"Member #{self.pk}"

    def generate_phone_otp(self) -> str:
        self.phone_otp = f"{secrets.randbelow(1000000):06d}"
        self.phone_otp_sent_at = timezone.now()
        self.save(update_fields=["phone_otp", "phone_otp_sent_at"])
        return self.phone_otp

    def otp_is_valid(self, code: str, ttl_minutes: int = 10) -> bool:
        if not self.phone_otp or not self.phone_otp_sent_at:
            return False
        if timezone.now() - self.phone_otp_sent_at > timedelta(minutes=ttl_minutes):
            return False
        return secrets.compare_digest(self.phone_otp, code.strip())
