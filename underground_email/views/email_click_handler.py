import logging

from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from underground_email.unsubscription import (
    decode_unsubscription_token,
    unsubscribe_from_tag_via_email_campaign,
)

logger = logging.getLogger(__name__)


@require_GET
def unsubscription_view(request: HttpRequest) -> HttpResponse:
    """Handle a recipient clicking their personalised unsubscribe link.

    The 'address' query parameter is a signed token produced by
    make_unsubscribe_token. Verifying the signature proves the request came
    from someone who actually received the email, even if they are not logged in.
    """
    token = request.GET.get("address", "")
    try:
        email, utm_id = decode_unsubscription_token(token)
    except (signing.BadSignature, KeyError, ValueError):
        logger.warning("Invalid unsubscription token received from %s: %s", request.user, token)
        return render(request, "underground_email/unsubscribe_invalid.html", status=400)

    campaign_title = unsubscribe_from_tag_via_email_campaign(utm_id, email)
    return render(
        request,
        "underground_email/unsubscribe_confirmed.html",
        {"campaign_title": campaign_title},
    )
