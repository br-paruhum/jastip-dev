"""Callbacks used by the Unfold admin configuration."""

from django.conf import settings


def environment_callback(request):
    """Show an environment badge in the admin header."""
    if settings.DEBUG:
        return ["Development", "warning"]
    # Production is the only environment that allows search indexing; staging
    # runs with ALLOW_INDEXING off, so it doubles as the prod/staging signal.
    if settings.ALLOW_INDEXING:
        return ["Production", "danger"]
    return ["Staging", "info"]
