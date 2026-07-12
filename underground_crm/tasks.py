import logging
import os
import uuid

from django.contrib.auth import get_user_model

from .models.address import Address
from .models.engagement import Engagement
from .models.pages import EventGuest
from .models.person import Tag

logger = logging.getLogger(__name__)

Person = get_user_model()


def geocode_address(address_id: str) -> None:
    """Call Addressr to geocode an Address and store the result."""
    from . import addressr as addressr_client

    try:
        address = Address.objects.get(pk=uuid.UUID(address_id))
    except Address.DoesNotExist:
        logger.warning("geocode_address: Address %s not found", address_id)
        return

    result = addressr_client.geocode(str(address))
    if result is None:
        logger.info("geocode_address: no result from Addressr for Address %s", address_id)
        return

    # Use .update() to avoid re-triggering the post_save signal.
    Address.objects.filter(pk=address.pk).update(
        latitude=result.latitude,
        longitude=result.longitude,
        geocode_reliability=result.reliability,
    )


def record_rsvp_engagement(event_guest_id: str) -> None:
    """
    Create an RSVP Engagement for an EventGuest — tagged even for
    unauthenticated RSVPs (EventGuest.person is only ever set for a real
    logged-in session — see FormSubmission.person's comment — so for an
    anonymous RSVP we resolve the same placeholder/matched Person that
    FormSubmissionForm.save() already created or confirmed by email).
    TODO: once we have a UI for this, surface whether a given engagement
    came from an authenticated or anonymous RSVP.
    """
    try:
        event_guest = EventGuest.objects.select_related("person", "page").get(
            pk=uuid.UUID(event_guest_id)
        )
    except EventGuest.DoesNotExist:
        logger.warning("record_rsvp_engagement: EventGuest %s not found", event_guest_id)
        return

    if event_guest.is_authenticated:
        person = event_guest.person
    else:
        try:
            person = Person.objects.get(email=event_guest.email_address)
        except Person.DoesNotExist:
            logger.warning(
                "record_rsvp_engagement: no Person found for email %s (EventGuest %s)",
                event_guest.email_address,
                event_guest_id,
            )
            return

    Engagement.objects.create(
        person=person,
        action_type=Engagement.RSVP,
        page_url=event_guest.page.slug,
        page_title=event_guest.page.title,
    )


def send_subscription_confirmation(person_id: str, tag_id: str) -> None:
    """
    Email a "confirm your subscription" verification link to a Person created
    by an unauthenticated mailing-list subscription (queued by the
    subscription_created receiver in signals.py).

    Verification state lives in allauth's EmailAddress model: the link points
    at allauth's account_confirm_email view, which marks the address verified
    on confirmation. The message goes out through SMTP2Go's HTTP API like all
    other outbound email — Django's SMTP email backend is not configured in
    deployments.
    """
    from allauth.account.models import EmailAddress, EmailConfirmationHMAC
    from django.conf import settings
    from django.template.loader import render_to_string
    from django.urls import reverse
    from smtp2go.core import Smtp2goClient

    try:
        person = Person.objects.get(pk=uuid.UUID(person_id))
        tag = Tag.objects.get(pk=uuid.UUID(tag_id))
    except (Person.DoesNotExist, Tag.DoesNotExist):
        logger.warning(
            "send_subscription_confirmation: Person %s or Tag %s not found", person_id, tag_id
        )
        return

    email_address, _created = EmailAddress.objects.get_or_create(
        user=person, email=person.email, defaults={"primary": True, "verified": False}
    )
    if email_address.verified:
        logger.info(
            "send_subscription_confirmation: %s is already verified; not emailing", person.email
        )
        return

    confirm_url = settings.WAGTAILADMIN_BASE_URL + reverse(
        "account_confirm_email", args=[EmailConfirmationHMAC(email_address).key]
    )
    html = render_to_string(
        "underground_crm/email/subscription_confirmation.html",
        {"person": person, "tag": tag, "confirm_url": confirm_url},
    )

    api_key = os.environ.get("SMTP_KEY")
    if not api_key:
        raise RuntimeError("SMTP_KEY environment variable is not set")
    response = Smtp2goClient(api_key=api_key).send(
        sender=settings.DEFAULT_FROM_EMAIL,
        recipients=[person.email],
        subject="Confirm your subscription",
        html=html,
    )
    if not response.success:
        logger.error(
            "send_subscription_confirmation: failed to send to %s: %s",
            person.email,
            response.errors,
        )
