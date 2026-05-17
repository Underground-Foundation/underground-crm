import logging
import uuid

from .models.address import Address
from .models.engagement import Engagement
from .models.pages import EventGuest

logger = logging.getLogger(__name__)


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
    """Create an RSVP Engagement for an EventGuest whose guest is a known Person."""
    try:
        event_guest = EventGuest.objects.select_related("guest", "event_page").get(
            pk=uuid.UUID(event_guest_id)
        )
    except EventGuest.DoesNotExist:
        logger.warning("record_rsvp_engagement: EventGuest %s not found", event_guest_id)
        return

    if event_guest.guest is None:
        return

    Engagement.objects.create(
        person=event_guest.guest,
        action_type=Engagement.RSVP,
        page_url=event_guest.event_page.slug,
        page_title=event_guest.event_page.subject,
    )


def revert_rsvp_engagement(event_guest_id: str) -> None:
    """Delete an RSVP Engagement for an EventGuest"""
    try:
        event_guest = EventGuest.objects.select_related("guest", "event_page").get(
            pk=uuid.UUID(event_guest_id)
        )
    except EventGuest.DoesNotExist:
        logger.warning("revert_rsvp_engagement: EventGuest %s not found", event_guest_id)
        return

    if event_guest.guest is None:
        return

    if event_guest.event_page.has_started is True:
        # Too late to revert their RSVP now
        return

    if not event_guest.event_page.slug:
        logger.warning(
            "revert_rsvp_engagement: Event %s lacks a full URL, so it will not be precise to delete corresponding engagement events",
            event_guest.event_page,
        )

    Engagement.objects.filter(
        person=event_guest.guest, action_type=Engagement.RSVP, page_url=event_guest.event_page.slug
    ).delete()
