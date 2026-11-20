"""
Unsubscription from our emails.

Each campaign email carries a link to the UnsubscriptionPage at /unsubscribe,
with a signed token naming its recipient (see make_unsubscription_url). We have
an unsubscription page, rather than unsubscribing people as soon as they follow
that link, to get around over-eager email clients and security scanners, which
follow the links in a message before its recipient has read it. Following the
link only shows the page; people unsubscribe by submitting its form (see
underground_email.unsubscription_form), which uses the functions below.
"""

import logging
import uuid
from typing import Optional

from django.conf import settings
from django.core import signing
from django.db.models import QuerySet

from underground_crm.models import Engagement
from underground_crm.models.membership import Membership
from underground_crm.models.person import PersonTag, Tag
from underground_email import app_settings

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
    """Decode and verify a signed token produced by generate_unsubscription_signature.

    Returns (email, utm_id). Raises signing.BadSignature if the token's signature doesn't match.
    """
    data = signing.loads(token, key=_signing_key(), salt=_SALT)
    return data["e"], data["c"]


def make_unsubscription_url(person: settings.AUTH_USER_MODEL, utm_id: str, root_url: str) -> str:
    """Build the absolute URL of the unsubscription page to embed in an outgoing email.

    Following it changes nothing by itself (see this module's docstring).
    """
    signature = generate_unsubscription_signature(email_address=person.email, utm_id=utm_id)
    return f"{root_url}/unsubscribe?utm_campaign={utm_id}&address={signature}"


def self_queryable_tags_of(person: settings.AUTH_USER_MODEL) -> QuerySet[Tag]:
    """The person's tags that are named in SELF_QUERYABLE_TAGS, which are the
    only ones a person is ever shown; the rest are for staff."""
    return person.tags.filter(name__in=settings.SELF_QUERYABLE_TAGS).order_by("name")


def remove_self_queryable_tag(
    person: settings.AUTH_USER_MODEL, tag_id: str, page_title: str
) -> Optional[Tag]:
    """Remove one of the SELF_QUERYABLE_TAGS from the person, at their own request.

    Only those tags may be removed this way, because they are the only ones a
    person is ever shown (see the API's tag query); the rest of a person's tags
    are for staff. The removal is logged as an "unsubscribed" Engagement.

    Returns the removed tag, or None if the person does not hold such a tag.
    """
    try:
        tag_id = uuid.UUID(tag_id)
    except ValueError:
        return None
    person_tag = (
        PersonTag.objects.select_related("tag")
        .filter(
            content_object=person,
            tag_id=tag_id,
            tag__name__in=settings.SELF_QUERYABLE_TAGS,
        )
        .first()
    )
    if person_tag is None:
        return None
    tag = person_tag.tag
    person_tag.delete()
    Engagement(
        person=person,
        action_type=Engagement.UNSUBSCRIBED,
        page_title=page_title,
        metadata={"tag": tag.name},
    ).save()
    logger.info("%s has unsubscribed from tag '%s'", person, tag)
    return tag


def cancel_own_membership(
    person: settings.AUTH_USER_MODEL, membership_id: str
) -> Optional[Membership]:
    """Cancel one of the person's current memberships, at their own request.

    Keyed on the person as well as the membership ID, so that a membership ID
    alone is never enough to cancel somebody else's membership.

    Returns the cancelled membership, or None if the person holds no such
    current membership.
    """
    try:
        membership_id = uuid.UUID(membership_id)
    except ValueError:
        return None
    membership = (
        Membership.objects.current()
        .select_related("type")
        .filter(person=person, pk=membership_id)
        .first()
    )
    if membership is None:
        return None
    membership.cancel()
    logger.info("%s has cancelled their %s membership", person, membership.type)
    return membership
