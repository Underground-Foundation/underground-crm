from django.urls import path

from underground_email.views.webhooks import email_webhook

urlpatterns = [
    # The link at the foot of each campaign email leads to the site's
    # SubscriptionPage, which Wagtail serves from its place in the page tree
    # (see underground_email.unsubscription.subscription_page_url).
    path("webhooks/email", email_webhook, name="email_webhook"),
]
