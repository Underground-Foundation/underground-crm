import json
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from underground_email.tasks import (
    _handle_spam,
    _handle_bounce,
    _handle_reject,
    _handle_unsubscription,
)

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def email_webhook(request: HttpRequest) -> HttpResponse:
    """Receive event notifications from smtp2go for delivery problems."""
    try:
        payload: dict[str, Any] = json.loads(request.body)
    except json.JSONDecodeError:
        logger.warning("smtp2go webhook received non-JSON body.")
        return HttpResponse(status=400)

    event = payload.get("event")
    recipient: str = payload.get("recipient", "")

    if event == "spam":
        _handle_spam(recipient, payload)
    elif event == "bounce":
        _handle_bounce(recipient, payload)
    elif event == "reject":
        _handle_reject(recipient, payload)
    elif event == "unsubscribe":
        _handle_unsubscription(recipient, payload)
    else:
        logger.warning("smtp2go webhook: unrecognised event type %r for %s.", event, recipient)

    # Always return 200 so smtp2go does not retry the delivery.
    return HttpResponse(status=200)
