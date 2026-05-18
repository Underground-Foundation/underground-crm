from django.urls import path

from . import views

urlpatterns = [
    path("<int:page_pk>/create-intent/", views.create_payment_intent, name="payment_create_intent"),
    path("webhook/", views.stripe_webhook, name="payment_stripe_webhook"),
]
