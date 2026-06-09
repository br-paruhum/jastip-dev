from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from apps.pages.sitemaps import SITEMAPS
from apps.pages.views import ads_txt, robots_txt

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("u/", include("apps.accounts.urls")),
    path("blog/", include("apps.blog.urls")),
    path("trips/", include("apps.trips.urls")),
    path("sitemap.xml", sitemap, {"sitemaps": SITEMAPS}, name="sitemap"),
    path("robots.txt", robots_txt, name="robots"),
    path("ads.txt", ads_txt, name="ads_txt"),
    path("", include("apps.pages.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
