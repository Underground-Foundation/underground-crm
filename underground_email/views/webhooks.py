import json
import logging
from typing import cast

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_q.tasks import async_task

from underground_email.api import SMTP2GoEventType, WebhookEmailDict

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def email_webhook(request: HttpRequest) -> HttpResponse:

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
        async_task(
            "underground_email.tasks.process_webhook_event",
            payload,
            recipient_email,
            cluster="email",
        )
    elif event_type in SMTP2GoEventType:
        logger.info("Ignoring %s event about %s", event_type, recipient_email)
    else:
        logger.warning(
            "smtp2go webhook: unrecognised event type %r for %s.", event_type, recipient_email
        )

    # Always return 200 so smtp2go does not retry the delivery.
    return HttpResponse(status=200)

