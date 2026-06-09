"""Pluggable WhatsApp provider interface.

Default implementation talks to the local Baileys microservice
(whatsapp-bot/). Swap `get_provider()` for a Cloud API implementation later
without touching call sites.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class WhatsAppError(Exception):
    pass


class BaseWhatsAppProvider:
    def send_message(self, to_e164: str, text: str) -> dict:
        raise NotImplementedError


class DisabledProvider(BaseWhatsAppProvider):
    """No-op used when WHATSAPP_ENABLED is false; logs instead of sending."""

    def send_message(self, to_e164: str, text: str) -> dict:
        logger.info("[whatsapp disabled] to=%s text=%r", to_e164, text)
        return {"ok": True, "disabled": True}


class BaileysProvider(BaseWhatsAppProvider):
    """Calls the Node Baileys service over HTTP."""

    def __init__(self, base_url: str, token: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def send_message(self, to_e164: str, text: str) -> dict:
        try:
            resp = requests.post(
                f"{self.base_url}/send",
                json={"to": to_e164, "message": text},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:  # pragma: no cover - network
            raise WhatsAppError(str(exc)) from exc


def get_provider() -> BaseWhatsAppProvider:
    if not getattr(settings, "WHATSAPP_ENABLED", False):
        return DisabledProvider()
    return BaileysProvider(settings.WHATSAPP_BOT_URL, settings.WHATSAPP_BOT_TOKEN)
