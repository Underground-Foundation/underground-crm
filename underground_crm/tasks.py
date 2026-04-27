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
        page_url=event_guest.event_page.full_url or "",
        page_title=event_guest.event_page.title,
    )
