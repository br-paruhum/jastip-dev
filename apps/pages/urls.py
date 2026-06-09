from django.urls import path

from . import views

app_name = "pages"

urlpatterns = [
    path("", views.home, name="home"),
    path("how-to/", views.how_to, name="how_to"),
    path("p/<slug:slug>/", views.page_detail, name="page"),
]
