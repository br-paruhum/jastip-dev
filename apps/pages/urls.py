from django.urls import path

from . import views

app_name = "pages"

urlpatterns = [
    path("", views.home, name="home"),
    path("how-to/", views.how_to, name="how_to"),
    path("how-to/for-buyer/", views.how_to_for_buyer, name="how_to_for_buyer"),
    path("how-to/for-traveler/", views.how_to_for_traveler, name="how_to_for_traveler"),
    path("become-a-carrier/", views.become_carrier, name="become_carrier"),
    path("contact/", views.contact, name="contact"),
    path("chat/", views.chat, name="chat"),
    path("p/<slug:slug>/", views.page_detail, name="page"),
]
