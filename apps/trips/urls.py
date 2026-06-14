from django.urls import path

from . import views

app_name = "trips"

urlpatterns = [
    path("plans/new/", views.plan_create, name="plan_create"),
    path("plans/<int:pk>/", views.plan_detail, name="plan_detail"),
    path("plans/<int:plan_id>/order/", views.request_create, name="request_create"),
    path("requests/<int:pk>/", views.request_detail, name="request_detail"),
    path("requests/<int:pk>/review/", views.request_review, name="request_review"),
    path("requests/<int:pk>/purchase/", views.request_purchase, name="request_purchase"),
    path("requests/<int:pk>/arrive/", views.request_arrive, name="request_arrive"),
    path("requests/<int:pk>/pay/", views.request_pay, name="request_pay"),
    path("requests/<int:pk>/clear/", views.request_clear, name="request_clear"),
    path("requests/<int:pk>/reship/", views.request_reship, name="request_reship"),
    path("requests/<int:pk>/reship-proof/", views.request_reship_proof, name="request_reship_proof"),
    path("requests/<int:pk>/message/", views.request_message, name="request_message"),
    path("requests/<int:pk>/refund-bank/", views.request_refund_bank, name="request_refund_bank"),
    path("kurs/", views.kurs, name="kurs"),
]
