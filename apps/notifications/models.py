from django.conf import settings
from django.db import models


class NotificationLog(models.Model):
    """Audit trail of every email / WhatsApp message the platform sends."""

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        WHATSAPP = "whatsapp", "WhatsApp"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    channel = models.CharField(max_length=10, choices=Channel.choices)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="notifications",
    )
    to_address = models.CharField(max_length=255, help_text="Email or phone (E.164).")
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    event = models.CharField(max_length=60, blank=True, help_text="e.g. request_received")

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_channel_display()} → {self.to_address} ({self.get_status_display()})"
