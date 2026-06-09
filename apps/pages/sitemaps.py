from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from apps.blog.models import Post

from .models import SitePage


class StaticViewSitemap(Sitemap):
    priority = 0.8
    changefreq = "weekly"

    def items(self):
        return ["pages:home", "pages:how_to", "blog:list"]

    def location(self, item):
        return reverse(item)


class BlogSitemap(Sitemap):
    priority = 0.6
    changefreq = "weekly"

    def items(self):
        return Post.objects.filter(status=Post.Status.PUBLISHED)

    def lastmod(self, obj):
        return obj.updated_at


class SitePageSitemap(Sitemap):
    priority = 0.5
    changefreq = "monthly"

    def items(self):
        return SitePage.objects.filter(is_published=True)

    def lastmod(self, obj):
        return obj.updated_at


SITEMAPS = {
    "static": StaticViewSitemap,
    "blog": BlogSitemap,
    "pages": SitePageSitemap,
}
