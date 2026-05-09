from django.urls import path

from underground_email.views.email_click_handler import unsubscription_view
from underground_email.views.webhooks import email_webhook

urlpatterns = [
    path("unsubscribe", unsubscription_view, name="unsubscribe"),
    path("webhooks/email", email_webhook, name="email_webhook"),
]
