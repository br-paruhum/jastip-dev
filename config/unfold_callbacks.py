"""Callbacks used by the Unfold admin configuration."""

from django.conf import settings


def environment_callback(request):
    """Show an environment badge in the admin header."""
    if settings.DEBUG:
        return ["Development", "warning"]
    return ["Production", "danger"]
