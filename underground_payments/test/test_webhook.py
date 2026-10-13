"""
Tests for the Stripe webhook and the donation flows that feed it, sending signed events
through the real stripe_webhook view.

Events are signed with Stripe's own library, using a test signing secret. The tests that
need Stripe objects create them in Stripe's test mode and replay the events Stripe itself
raised for them, so they run only when STRIPE_SECRET_KEY is a test-mode key.

django-q runs each queued task in-process, through its documented "sync" mode, so that
the task the webhook hands off is the real one. django-q reads Q_CLUSTER once, on import,
which is why the tests switch its Conf.SYNC rather than overriding the setting.
"""

import json
import time
from decimal import Decimal
from typing import Any
from unittest import mock

import stripe
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django_q.conf import Conf
from djmoney.money import Money
from wagtail.models import Page

from underground_crm.models import Donation, Engagement
from underground_payments.models import PaymentPage

WEBHOOK_SECRET = "whsec_test_fusion_party_webhook_signing_secret"
DONOR_EMAIL = "kerryn.abbott@example.org"
# Stripe's test card that always succeeds.
TEST_CARD = "pm_card_visa"
# How long to wait for Stripe to make an event it raised available through its API.
EVENT_WAIT_SECONDS = 30
EVENT_POLL_INTERVAL_SECONDS = 1


def signature_header(payload: str, secret: str = WEBHOOK_SECRET) -> str:
    """The Stripe-Signature header Stripe would send with this payload."""
    timestamp = int(time.time())
    # The function Stripe's library uses to compute the signature it then verifies.
    signature = stripe.WebhookSignature._compute_signature(f"{timestamp}.{payload}", secret)
    return f"t={timestamp},{stripe.WebhookSignature.EXPECTED_SCHEME}={signature}"


def test_mode_key() -> str:
    key = settings.STRIPE_SECRET_KEY
    return key if key.startswith("sk_test_") else ""


@override_settings(STRIPE_WEBHOOK_SECRET=WEBHOOK_SECRET)
class WebhookTestCase(TestCase):
    def setUp(self):
        sync = mock.patch.object(Conf, "SYNC", True)
        sync.start()
        self.addCleanup(sync.stop)
        self.page = PaymentPage(
            title="Donate to Fusion", slug="donate", allow_monthly_payments=True
        )
        Page.objects.get(id=1).add_child(instance=self.page)

    def page_metadata(self, frequency: str) -> dict[str, str]:
        """The metadata create_payment_intent puts on a donation made through the page."""
        return {
            "page_id": str(self.page.pk),
            "page_url": "https://fusionparty.org.au/donate/",
            "frequency": frequency,
            "donor_email": DONOR_EMAIL,
            "donor_first_name": "Kerryn",
            "donor_last_name": "Abbott",
        }

    def post_event(self, payload: str, signature: str | None = None):
        return self.client.post(
            reverse("payment_stripe_webhook"),
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=signature or signature_header(payload),
        )


def payment_intent_succeeded_event(payment_intent: dict[str, Any]) -> str:
    """A payment_intent.succeeded event, shaped as Stripe sends it."""
    return json.dumps(
        {
            "id": "evt_3SeWXo2EiAKqJzIt0vgZtQ9b",
            "object": "event",
            "api_version": "2026-04-22.dahlia",
            "created": int(time.time()),
            "livemode": False,
            "pending_webhooks": 1,
            "type": "payment_intent.succeeded",
            "data": {"object": payment_intent},
        }
    )


class OneOffDonationWebhookTests(WebhookTestCase):
    """A synthetic one-off payment, which needs no Stripe API calls to record."""

    AMOUNT_CENTS = 2500

    def payment_intent(self) -> dict[str, Any]:
        return {
            "id": "pi_3SeWXo2EiAKqJzIt1kQ7vM4d",
            "object": "payment_intent",
            "amount": self.AMOUNT_CENTS,
            "amount_received": self.AMOUNT_CENTS,
            "currency": "aud",
            "status": "succeeded",
            "livemode": False,
            "metadata": self.page_metadata(frequency="once"),
        }

    def test_records_the_donation(self):
        payment_intent = self.payment_intent()
        response = self.post_event(payment_intent_succeeded_event(payment_intent))

        self.assertEqual(response.status_code, 200)
        donation = Donation.objects.get(stripe_payment_id=payment_intent["id"])
        self.assertEqual(donation.person.email, DONOR_EMAIL)
        self.assertEqual(donation.amount, Money(Decimal(self.AMOUNT_CENTS) / 100, "AUD"))
        self.assertFalse(donation.is_recurring)
        self.assertTrue(
            Engagement.objects.filter(
                person=donation.person, action_type=Engagement.DONATED
            ).exists()
        )

    def test_redelivered_event_is_recorded_once(self):
        payload = payment_intent_succeeded_event(self.payment_intent())
        self.post_event(payload)
        self.post_event(payload)
        self.assertEqual(
            Donation.objects.count(),
            1,
            "Stripe may deliver an event more than once, but it is one payment.",
        )

    def test_rejects_an_event_signed_with_another_secret(self):
        payload = payment_intent_succeeded_event(self.payment_intent())
        response = self.post_event(
            payload, signature=signature_header(payload, secret="whsec_test_someone_else")
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Donation.objects.exists())


class StripeTestModeWebhookTests(WebhookTestCase):
    """
    Real payments in Stripe's test mode, whose own events are replayed to the webhook.
    """

    AMOUNT_CENTS = 500

    def setUp(self):
        key = test_mode_key()
        if not key:
            self.skipTest("STRIPE_SECRET_KEY is not a test-mode key.")
        super().setUp()
        self.stripe = stripe.StripeClient(key)
        self.started_at = int(time.time())

    def create_customer(self) -> stripe.Customer:
        customer = self.stripe.v1.customers.create(
            params={
                "email": DONOR_EMAIL,
                "payment_method": TEST_CARD,
                "invoice_settings": {"default_payment_method": TEST_CARD},
            }
        )
        # Deleting a Customer also cancels their subscriptions.
        self.addCleanup(self.stripe.v1.customers.delete, customer.id)
        return customer

    def stripe_event(self, event_type: str, object_id: str) -> str:
        """The JSON of the event Stripe raised about the object, once it is available."""
        deadline = time.monotonic() + EVENT_WAIT_SECONDS
        while time.monotonic() < deadline:
            events = self.stripe.v1.events.list(
                params={"type": event_type, "created": {"gte": self.started_at}}
            )
            for event in events.data:
                if event.data.object.id == object_id:
                    return json.dumps(event.to_dict(for_json=True))
            time.sleep(EVENT_POLL_INTERVAL_SECONDS)
        self.fail(f"Stripe raised no {event_type} event for {object_id} within the wait.")

    def test_one_off_payment_is_recorded(self):
        customer = self.create_customer()
        payment_intent = self.stripe.v1.payment_intents.create(
            params={
                "amount": self.AMOUNT_CENTS,
                "currency": "aud",
                "customer": customer.id,
                "payment_method": TEST_CARD,
                "payment_method_types": ["card"],
                "confirm": True,
                "metadata": self.page_metadata(frequency="once"),
            }
        )

        response = self.post_event(self.stripe_event("payment_intent.succeeded", payment_intent.id))

        self.assertEqual(response.status_code, 200)
        donation = Donation.objects.get(stripe_payment_id=payment_intent.id)
        self.assertEqual(donation.amount, Money(Decimal(self.AMOUNT_CENTS) / 100, "AUD"))
        self.assertFalse(donation.is_recurring)

    def test_subscription_payment_is_recorded(self):
        customer = self.create_customer()
        price = self.stripe.v1.prices.create(
            params={
                "unit_amount": self.AMOUNT_CENTS,
                "currency": "aud",
                "recurring": {"interval": "month"},
                "product_data": {"name": f"Recurring donation — {self.page.title}"},
            }
        )
        subscription = self.stripe.v1.subscriptions.create(
            params={
                "customer": customer.id,
                "items": [{"price": price.id}],
                "metadata": self.page_metadata(frequency="monthly"),
            }
        )
        invoice_payments = self.stripe.v1.invoice_payments.list(
            params={"invoice": subscription.latest_invoice}
        )
        paying_intent_id = invoice_payments.data[0].payment.payment_intent

        response = self.post_event(self.stripe_event("invoice.paid", subscription.latest_invoice))

        self.assertEqual(response.status_code, 200)
        # The Donation is keyed by the PaymentIntent that paid the invoice.
        donation = Donation.objects.get(stripe_payment_id=paying_intent_id)
        self.assertEqual(donation.amount, Money(Decimal(self.AMOUNT_CENTS) / 100, "AUD"))
        self.assertTrue(donation.is_recurring)
        self.assertEqual(donation.metadata["frequency"], "monthly")


class MonthlySignUpTests(WebhookTestCase):
    """Starting a monthly donation on a PaymentPage, against Stripe's test mode."""

    def setUp(self):
        if not test_mode_key():
            self.skipTest("STRIPE_SECRET_KEY is not a test-mode key.")
        super().setUp()

    def test_returns_the_client_secret_of_the_first_payment(self):
        response = self.client.post(
            reverse("payment_create_intent", args=[self.page.pk]),
            data=json.dumps(
                {
                    "amount_cents": 2600,
                    "frequency": "monthly",
                    "email": DONOR_EMAIL,
                    "first_name": "Kerryn",
                    "last_name": "Abbott",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        client = stripe.StripeClient(test_mode_key())
        subscription = client.v1.subscriptions.retrieve(body["subscription_id"])
        self.addCleanup(client.v1.customers.delete, subscription.customer)
        self.assertEqual(body["type"], "subscription")
        # A PaymentIntent's client secret is its ID followed by "_secret_", which is
        # what stripe.confirmPayment() in the page's JavaScript needs.
        self.assertRegex(body["client_secret"], r"^pi_\w+_secret_\w+$")
