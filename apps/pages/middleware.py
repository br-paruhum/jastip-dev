from django.conf import settings


class NoindexMiddleware:
    """Emit `X-Robots-Tag: noindex, nofollow` on every response unless
    ALLOW_INDEXING is true. Keeps staging/dev out of search indexes even for
    non-HTML responses (sitemap, feeds), complementing robots.txt."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not getattr(settings, "ALLOW_INDEXING", False):
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response
