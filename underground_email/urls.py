from django.urls import path

from underground_email.views.email_click_handler import subscriptions_view
from underground_email.views.webhooks import email_webhook

urlpatterns = [
    # The link at the foot of each campaign email leads here, to the SubscriptionPage.
    path("unsubscribe", subscriptions_view, name="unsubscribe"),
    path("webhooks/email", email_webhook, name="email_webhook"),
]
