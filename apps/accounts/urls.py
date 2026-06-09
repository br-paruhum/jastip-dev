from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("profile/", views.profile, name="profile"),
    path("profile/update/", views.profile_update, name="profile_update"),
    path("profile/send-otp/", views.send_otp, name="send_otp"),
    path("profile/verify-otp/", views.verify_otp, name="verify_otp"),
]
