from django.urls import path

from underground_email.views.email_click_handler import subscriptions_view
from underground_email.views.webhooks import email_webhook

urlpatterns = [
    # The link at the foot of each campaign email leads here. Links in emails sent
    # before this page was renamed lead to /unsubscribe, which a Wagtail Redirect
    # (created by migration 0020) sends here.
    path("subscriptions", subscriptions_view, name="subscriptions"),
    path("webhooks/email", email_webhook, name="email_webhook"),
]
