import logging
import uuid

from django.contrib.auth import get_user_model

from .models.address import Address
from .models.engagement import Engagement
from .models.pages import EventGuest

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
