import logging
import uuid

from .models.engagement import Engagement
from .models.pages import EventGuest

logger = logging.getLogger(__name__)


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
        page_title=event_guest.event_page.title,
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
