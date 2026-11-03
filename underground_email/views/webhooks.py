import hmac
import json
import logging
from typing import Any, cast

from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from underground_email.api import SMTP2GoEventType, WebhookEmailDict
from underground_email.tasks import (
    get_smtp2go_key,
    handle_spam_or_unsubscription,
)

logger = logging.getLogger(__name__)


def _is_authorized(request: HttpRequest) -> bool:
    """True if the request carries the secret we gave to SMTP2Go when registering
    this webhook. Without this check, anybody could report a person as a spam
    complainant and have them opted out, because the view is public and exempt from
    CSRF checks. If the secret is not configured, _get_smtp2go_key raises an exception."""
    scheme, _separator, token = request.headers.get("Authorization", "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(
        token.encode(), get_smtp2go_key().encode()
    )


@csrf_exempt
@require_POST
def email_webhook(request: HttpRequest) -> HttpResponse:
    if not _is_authorized(request):
        logger.warning("Refusing an SMTP2Go webhook delivery without the correct secret.")
        return HttpResponse(status=401)

    try:
        payload = cast(WebhookEmailDict, json.loads(request.body))
    except json.JSONDecodeError:
        logger.warning("SMTP2Go webhook received non-JSON body.")
        return HttpResponse(status=400)

    event_type = payload.get("event")
    recipient_email: str = payload.get("rcpt")
    if not recipient_email:
        logger.error("No recipient was specified in this webhook body: %s", request.body)
        return HttpResponse(status=200)  # No retries are going to happen anyway
    if event_type in (SMTP2GoEventType.SPAM, SMTP2GoEventType.UNSUBSCRIBED):
        Person = get_user_model()
        recipient = Person.objects.filter(
            email=Person.objects.normalize_email(recipient_email)
        ).first()
        if recipient is None:
            logger.warning("Ignoring %s event about unknown person %s", event_type, recipient_email)
            return HttpResponse(status=200)
        handle_spam_or_unsubscription(payload, recipient=recipient)
    elif event_type in SMTP2GoEventType:
        logger.info("Ignoring %s event about %s", event_type, recipient_email)
    else:
        logger.warning(
            "smtp2go webhook: unrecognised event type %r for %s.", event_type, recipient_email
        )

    # Always return 200 so smtp2go does not retry the delivery.
    return HttpResponse(status=200)
