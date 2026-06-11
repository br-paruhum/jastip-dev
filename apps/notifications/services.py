"""Send-and-log helpers for email + WhatsApp notifications."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import close_old_connections
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from .models import NotificationLog
from .whatsapp import WhatsAppError, get_provider

logger = logging.getLogger(__name__)

# Bounded pool so notification I/O (SMTP handshake, WhatsApp HTTP) runs off the
# request/response cycle. Each gunicorn worker gets its own pool. Best-effort:
# tasks pending when a worker is recycled are dropped, which is fine for
# notifications (every send is also recorded in NotificationLog).
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="notify")


def _site_url(path: str = "") -> str:
    domain = getattr(settings, "SITE_DOMAIN", "localhost:8019")
    scheme = "http" if settings.DEBUG else "https"
    return f"{scheme}://{domain}{path}"


def _logo_url() -> str:
    base = settings.STATIC_URL if settings.STATIC_URL.startswith("/") else "/" + settings.STATIC_URL
    return _site_url(base + "img/logo-email.png")


def _run_off_request(fn):
    """Run a blocking send in the background, unless NOTIFICATIONS_ASYNC is off
    (under tests) in which case run it inline so assertions stay deterministic."""
    if not getattr(settings, "NOTIFICATIONS_ASYNC", True):
        return fn()

    def _task():
        try:
            fn()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Background notification task failed")
        finally:
            # Release this thread's DB connection so it isn't leaked/reused stale.
            close_old_connections()

    _executor.submit(_task)
    return None


def deliver_in_background(send_callable):
    """Public hook for sending an already-built message (e.g. allauth's
    EmailMessage.send) off the request cycle, honouring NOTIFICATIONS_ASYNC."""
    return _run_off_request(send_callable)


def send_email(*, to_user=None, to_address=None, subject, template, context=None, event="",
               cc_admin=True, attachments=None):
    """Render an HTML email (with plaintext fallback) and log it.

    Per spec, the admin is cc'd on all traveler/buyer correspondence.
    `attachments` is an optional list of (filename, content_bytes, mimetype).
    """
    context = context or {}
    # email_base.html header needs an absolute logo URL; default it so any
    # caller (not just the trips workflow) gets the branded header.
    context.setdefault("logo_url", _logo_url())
    context.setdefault("site_url", _site_url("/"))
    address = to_address or (to_user.email if to_user else None)
    if not address:
        return None

    def _do():
        html_body = render_to_string(f"emails/{template}.html", context)
        text_body = strip_tags(html_body)

        cc = []
        if cc_admin and settings.ADMIN_EMAIL and address != settings.ADMIN_EMAIL:
            cc = [settings.ADMIN_EMAIL]

        log = NotificationLog.objects.create(
            channel=NotificationLog.Channel.EMAIL,
            recipient=to_user,
            to_address=address,
            subject=subject,
            body=text_body,
            event=event,
        )
        try:
            msg = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [address], cc=cc)
            msg.attach_alternative(html_body, "text/html")
            for filename, content, mimetype in (attachments or []):
                msg.attach(filename, content, mimetype)
            msg.send()
            log.status = NotificationLog.Status.SENT
        except Exception as exc:  # pragma: no cover - smtp
            log.status = NotificationLog.Status.FAILED
            log.error = str(exc)
            logger.exception("Email send failed to %s", address)
        log.save(update_fields=["status", "error"])
        return log

    return _run_off_request(_do)


def send_whatsapp(*, to_user=None, to_e164=None, text, event=""):
    """Send a WhatsApp message via the configured provider and log it."""
    number = to_e164 or (to_user.phone_e164 if to_user else None)
    if not number:
        return None

    def _do():
        log = NotificationLog.objects.create(
            channel=NotificationLog.Channel.WHATSAPP,
            recipient=to_user,
            to_address=number,
            body=text,
            event=event,
        )
        try:
            get_provider().send_message(number, text)
            log.status = NotificationLog.Status.SENT
        except WhatsAppError as exc:
            log.status = NotificationLog.Status.FAILED
            log.error = str(exc)
            logger.warning("WhatsApp send failed to %s: %s", number, exc)
        log.save(update_fields=["status", "error"])
        return log

    return _run_off_request(_do)


def notify_see_email(user, event=""):
    """The recurring 'Please see your email' WhatsApp reminder."""
    return send_whatsapp(to_user=user, text="Jastip.me: Please see your email.", event=event)
