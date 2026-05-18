import json
import logging

import stripe
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

_CURRENCY = "aud"
_MIN_AMOUNT_CENTS = 100  # $1.00


def _stripe_client() -> stripe.StripeClient:
    return stripe.StripeClient(settings.STRIPE_SECRET_KEY)


@require_POST
def create_payment_intent(request, page_pk: int):
    """
    Create a Stripe PaymentIntent (one-time) or Subscription (monthly/annual) for
    a donation to the given PaymentPage.

    Request body (JSON):
        amount_cents  int    Amount in AUD cents, e.g. 1000 for $10.00
        frequency     str    "once", "monthly", or "annual"
        email         str    Donor email address

    Response (JSON):
        client_secret    str   Stripe client secret for stripe.confirmPayment()
        type             str   "payment_intent" or "subscription"
        subscription_id  str   Present only when type == "subscription"
    """
    from .models import PaymentPage

    page = get_object_or_404(PaymentPage, pk=page_pk, live=True)

    try:
        data = json.loads(request.body)
        amount_cents = int(data["amount_cents"])
        frequency = data.get("frequency", "once")
        email = data.get("email", "").strip()
        first_name = data.get("first_name", "").strip()[:100]
        last_name = data.get("last_name", "").strip()[:100]
        address_line1 = data.get("address_line1", "").strip()[:200]
        city = data.get("city", "").strip()[:100]
        state = data.get("state", "").strip()[:100]
        postcode = data.get("postcode", "").strip()[:20]
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return JsonResponse({"error": "Invalid request body."}, status=400)

    if amount_cents < _MIN_AMOUNT_CENTS:
        return JsonResponse({"error": "Minimum donation is $1.00."}, status=400)

    if frequency not in ("once", "monthly", "annual"):
        return JsonResponse({"error": "Invalid frequency."}, status=400)

    if frequency == "monthly" and not page.allow_monthly_payments:
        return JsonResponse(
            {"error": "Monthly payments are not enabled for this page."}, status=400
        )

    if frequency == "annual" and not page.allow_annual_payments:
        return JsonResponse({"error": "Annual payments are not enabled for this page."}, status=400)

    metadata = {
        "page_id": str(page.pk),
        "page_url": request.build_absolute_uri(page.url),
        "frequency": frequency,
        "donor_email": email,
        "donor_first_name": first_name,
        "donor_last_name": last_name,
        "donor_address_line1": address_line1,
        "donor_city": city,
        "donor_state": state,
        "donor_postcode": postcode,
    }

    client = _stripe_client()
    try:
        if frequency == "once":
            intent = client.payment_intents.create(
                params={
                    "amount": amount_cents,
                    "currency": _CURRENCY,
                    "metadata": metadata,
                    "receipt_email": email or None,
                    "automatic_payment_methods": {"enabled": True},
                }
            )
            return JsonResponse({"client_secret": intent.client_secret, "type": "payment_intent"})

        interval = "month" if frequency == "monthly" else "year"
        customer = client.customers.create(
            params={"email": email or None, "metadata": {"source": "payment_page"}}
        )
        price = client.prices.create(
            params={
                "unit_amount": amount_cents,
                "currency": _CURRENCY,
                "recurring": {"interval": interval},
                "product_data": {"name": f"Recurring donation — {page.title}"},
            }
        )
        subscription = client.subscriptions.create(
            params={
                "customer": customer.id,
                "items": [{"price": price.id}],
                "payment_behavior": "default_incomplete",
                "payment_settings": {"save_default_payment_method": "on_subscription"},
                "metadata": metadata,
                "expand": ["latest_invoice.payment_intent"],
            }
        )
        pi_secret = subscription.latest_invoice.payment_intent.client_secret
        return JsonResponse(
            {
                "client_secret": pi_secret,
                "type": "subscription",
                "subscription_id": subscription.id,
            }
        )
    except stripe.StripeError as exc:
        logger.error("Stripe error creating payment for page %s: %s", page_pk, exc)
        return JsonResponse({"error": exc.user_message or "Payment setup failed."}, status=400)


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """
    Handle Stripe webhook events. Must be registered in the Stripe dashboard
    for at least: payment_intent.succeeded, invoice.paid.
    """
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        logger.warning("Invalid Stripe webhook signature.")
        return HttpResponse(status=400)

    if event.type == "payment_intent.succeeded":
        pi = event.data.object
        meta = pi.get("metadata") or {}
        if meta.get("page_id"):
            from django_q.tasks import async_task

            async_task(
                "underground_payments.tasks.handle_successful_donation",
                pi["id"],
                pi["amount"],
                dict(meta),
            )

    elif event.type == "invoice.paid":
        invoice = event.data.object
        # Subscription renewals: fetch the subscription to get page metadata.
        subscription_id = invoice.get("subscription")
        pi_id = invoice.get("payment_intent")
        amount_paid = invoice.get("amount_paid", 0)
        if subscription_id and pi_id and amount_paid:
            from django_q.tasks import async_task

            async_task(
                "underground_payments.tasks.handle_successful_subscription_renewal",
                pi_id,
                amount_paid,
                subscription_id,
            )

    return HttpResponse(status=200)
