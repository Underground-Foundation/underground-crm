from django.urls import path

from underground_email.views.webhooks import email_webhook

urlpatterns = [
    # At the bottom of each campaign email, there is a link to a
    # SubscriptionPage, intended for unsubscribing. See
    # underground_email.unsubscription.subscription_page_url.
    path("webhooks/email", email_webhook, name="email_webhook"),
]
