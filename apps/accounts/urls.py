from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("dashboard/", views.profile, name="profile"),
    path("dashboard/update/", views.profile_update, name="profile_update"),
    path("dashboard/password/", views.password_change, name="password_change"),
    path("dashboard/send-otp/", views.send_otp, name="send_otp"),
    path("dashboard/verify-otp/", views.verify_otp, name="verify_otp"),
]
