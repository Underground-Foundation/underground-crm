import logging
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.utils import timezone

from underground_crm.models import Engagement
from underground_crm.models.person import PersonTag
from underground_email import app_settings
from underground_email.models import EmailCampaign
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

_SALT = "underground_email.unsubscribe"


def _signing_key() -> str:
    return app_settings.UNSUBSCRIBE_SIGNING_KEY or settings.SECRET_KEY


def generate_unsubscription_signature(email_address: str, utm_id: str) -> str:
    """Return a signed token encoding this recipient's email address and campaign ID.

    The token is HMAC-signed; forging or altering it requires knowledge of
    UNSUBSCRIBE_SIGNING_KEY, so a link in a delivered email can be trusted as
    proof that the recipient actually received the message.
    """
    return signing.dumps(
        {"e": email_address, "c": utm_id},
        key=_signing_key(),
        salt=_SALT,
    )


def decode_unsubscription_token(token: str) -> tuple[str, str]:
    """Decode and verify a signed token produced by make_unsubscribe_token.

    Returns (email, utm_id). Raises signing.BadSignature if the token's signature doesn't match.
    """
    data = signing.loads(token, key=_signing_key(), salt=_SALT)
    return data["e"], data["c"]


def make_unsubscription_url(person: settings.AUTH_USER_MODEL, utm_id: str) -> str:
    """Build the absolute unsubscribe URL to embed in an outgoing email."""
    signature = generate_unsubscription_signature(email_address=person.email, utm_id=utm_id)
    base = settings.WAGTAILADMIN_BASE_URL.rstrip("/")
    return f"{base}/unsubscribe?utm_campaign={utm_id}&address={signature}"


def unsubscribe_from_tag_via_email_campaign(
    utm_campaign_id: str, email_address: str
) -> Optional[str]:
    """Record the unsubscription for the person with the given email address.

    If the campaign has an unsubscription tag, only that tag is removed from the
    person's profile. Without one, the person is marked as fully unsubscribed
    from all email (a much more drastic action, logged accordingly).

    Returns the tag for which users shall no longer receive emails (or None, if they have subscribed from all emails)
    """
    User = get_user_model()
    try:
        person = User.objects.get(email=email_address, unsubscribed_at__isnull=False)
    except User.DoesNotExist:
        logger.warning("Unsubscription failed for %s", email_address)
        raise ValueError(
            _("No current user found with email address %(email)s") % {"email": email_address}
        )

    campaign = EmailCampaign.objects.select_related("unsubscription_tag").get(
        utm_id=utm_campaign_id
    )
    # Maybe we should reject the unsubscription request if it comes from a very old email.

    Engagement(
        person=person,
        action_type=Engagement.UNSUBSCRIBED,
        page_title=campaign.subject,
        metadata={"campaign_utm_id": utm_campaign_id},
    ).save()
    campaign.unsubscription_count = (campaign.unsubscription_count or 0) + 1
    campaign.save(update_fields=["unsubscription_count"])

    if campaign.unsubscription_tag:
        try:
            person_tag = PersonTag.objects.get(tag=campaign.unsubscription_tag, person_id=person.id)
        except PersonTag.DoesNotExist:
            logger.info(
                "%s was not tagged with '%s'; unsubscription is already effective.",
                person,
                campaign.unsubscription_tag,
            )
        else:
            person_tag.delete()
        return campaign.unsubscription_tag.name
    else:
        logger.warning(
            "%s is unsubscribing from all future emails after receiving campaign %s (%s).",
            person,
            utm_campaign_id,
            campaign.subject,
        )
        person.unsubscribed_at = timezone.now()
        person.is_supporter = False
        person.is_prospect = False
        person.is_active = False  # Party members are required to receive some emails
        person.save(update_fields=["unsubscribed_at", "is_supporter", "is_prospect", "is_active"])
    return None
