import logging
import os
import datetime
from os import sched_get_priority_max
from types import new_class
from typing import Any, Dict, cast, List, Union, Tuple, Optional

import requests
from cachetools import cached
from django.conf import settings
from django.contrib.auth import get_user_model
from django.template import Context, Template as DjangoTemplate
from django.template.loader import render_to_string
from django.utils import timezone
from smtp2go.core import Smtp2goClient

from underground_email import app_settings
from underground_email.api import (
    SMTP2GoEventType,
    BAD_OUTCOMES,
    SMTPActivityResponse,
    SMTPEvent,
    WebhookEmailDict,
    POSITIVE_ENGAGEMENT_OUTCOMES,
)
from underground_email.models import EmailCampaign
from underground_email.unsubscription import make_unsubscription_url

logger = logging.getLogger(__name__)
_ACTIVITY_BATCH_SIZE = 1_000  # https://developers.SMTP2Go.com/reference/activity-search


@cached({})
def _api_key() -> str:
    key = os.environ.get("SMTP_KEY")
    if not key:
        raise RuntimeError("SMTP_KEY environment variable is not set")
    return key


def _render_greeting(greeting_template: str, person) -> str:
    """Render a TemplatedGreeting template string for a specific recipient."""
    t = DjangoTemplate(greeting_template)
    return t.render(
        Context(
            {
                "first_name": person.first_name or "",
                "last_name": person.last_name or "",
                "full_name": person.full_name,
            }
        )
    )


def _render_email_html(campaign: EmailCampaign, person) -> str:
    """Render the campaign's email template for a specific recipient."""
    from django.conf import settings

    return render_to_string(
        campaign.template,
        {
            "subject": campaign.subject,
            "greeting": (
                _render_greeting(campaign.greeting.greeting, person) if campaign.greeting else ""
            ),
            "body": campaign.body,
            "signature": None if not campaign.sender else campaign.sender.signature,
            "preview_text": campaign.preview_text,
            "unsubscription_url": make_unsubscription_url(person, campaign.utm_id),
            "recipient_email": person.email,
            "site_url": settings.WAGTAILADMIN_BASE_URL.rstrip("/"),
        },
    )


def send_emails(campaign_utm_id: str) -> None:
    """
    Upload the campaign template to SMTP2Go and dispatch personalised emails to
    all eligible recipients using the batch send endpoint. Recipients are
    processed in batches of 1,000 to keep request sizes manageable.

    After all batches are dispatched, a results-check task is scheduled to run
    24 hours later.
    """
    from .models import EmailCampaign

    logger.info("send_emails started for campaign %s.", campaign_utm_id)

    campaign = EmailCampaign.objects.select_related(
        "sender__sender", "greeting", "people_filter"
    ).get(utm_id=campaign_utm_id)

    recipients_qs = campaign.get_potential_recipients()
    logger.info(
        "Campaign %s (%r): %s eligible recipient(s) found.",
        campaign_utm_id,
        campaign.subject,
        recipients_qs.count() if recipients_qs is not None else "no filter set — 0",
    )
    if not recipients_qs:
        logger.warning("Campaign %s has no eligible recipients; marking as sent.", campaign_utm_id)
        campaign.state = 2
        campaign.save(update_fields=["state"])
        return

    # The SMTP2Go client library only wraps the single-send endpoint, so we
    # send one request per recipient to support per-person personalisation.
    client = Smtp2goClient(api_key=_api_key())
    sender_person = campaign.sender.sender
    sender_str = f"{sender_person.full_name} <{sender_person.email}>"

    all_recipients = list(recipients_qs.only("id", "email", "first_name", "last_name"))
    sent_count = 0

    for person in all_recipients:
        try:
            response = client.send(
                sender=sender_str,
                recipients=[f"{person.full_name} <{person.email}>"],
                subject=campaign.subject,
                html=_render_email_html(campaign, person),
            )
            if response.success:
                sent_count += 1
            else:
                logger.error(
                    "Campaign %s: failed to send to %s: %s",
                    campaign_utm_id,
                    person.email,
                    response.errors,
                )
        except Exception:
            logger.exception(
                "Campaign %s: exception sending to %s.",
                campaign_utm_id,
                person.email,
            )

    campaign.state = 2
    campaign.sent_count = sent_count
    updated = ["state", "sent_count"]
    if not campaign.sending_date:
        campaign.sending_date = datetime.datetime.now(tz=datetime.timezone.utc)
        updated.append("sending_date")
    campaign.save(update_fields=updated)
    logger.info("Campaign %s sent to %d recipients.", campaign_utm_id, sent_count)

    # Schedule a results check 24 hours after sending completes.
    from django_q.models import Schedule
    from django_q.tasks import schedule as q_schedule

    q_schedule(
        "underground_email.tasks.get_email_results",
        campaign_utm_id,
        task_name=f"email_results_{campaign_utm_id}",
        schedule_type=Schedule.ONCE,
        next_run=timezone.now() + datetime.timedelta(hours=24),
    )


def process_email_engagements(
    campaign: EmailCampaign,
    recipients: Dict[str, settings.AUTH_USER_MODEL],
    events: List[SMTPEvent],
    persist=True,
) -> Tuple[int, int, int]:
    from underground_crm.models import Engagement

    # Notice we avoid the double-counting of people.
    recognized_engagement_count = 0
    new_opens, new_clicks = 0, 0
    for event_type in POSITIVE_ENGAGEMENT_OUTCOMES:
        action_type = (
            Engagement.EMAIL_OPENED
            if event_type == SMTP2GoEventType.OPENED
            else Engagement.EMAIL_CLICKED
        )
        already_engaged: set = set(
            Engagement.objects.filter(
                action_type=action_type,
                metadata__campaign_utm_id=campaign.utm_id,
            ).values_list("person_id", flat=True)
        )

        new_engagements = []
        for event in events:
            if event["event"] != event_type.value:
                continue
            person = recipients.get(event["recipient"])
            if not person:
                logger.error(
                    "Recipient %s must have been removed from our database after being sent this email (%s): %s",
                    event["recipient"],
                    campaign.utm_id,
                    campaign.subject,
                )
                continue
            if person.pk in already_engaged:
                recognized_engagement_count += 1
            else:
                new_engagements.append(
                    Engagement(
                        person=person,
                        action_type=action_type,
                        page_title=campaign.subject,
                        metadata={"campaign_utm_id": campaign.utm_id},
                    )
                )

            if new_engagements:
                if persist:
                    Engagement.objects.bulk_create(new_engagements)
                logger.info(
                    "%d new %s engagements for campaign %s.",
                    len(new_engagements),
                    event_type,
                    campaign.utm_id,
                )

        if action_type == Engagement.EMAIL_OPENED:
            campaign.opened_count = (campaign.opened_count or 0) + len(new_engagements)
            new_opens += len(new_engagements)
        else:
            campaign.clicked_count = (campaign.clicked_count or 0) + len(new_engagements)
            new_clicks += len(new_engagements)
    return recognized_engagement_count, new_opens, new_clicks


def handle_spam_or_unsubscription(
    event: Union[WebhookEmailDict, SMTPEvent], recipient, persist=True
):
    updated_fields = ["unsubscribed_at"]
    if event.get("event") == SMTP2GoEventType.SPAM:
        # This act will make emails harder to deliver to everyone else.
        logger.warning("User %s 🗡 marked this email as spam: %s", recipient, event["subject"])
        # todo: add a person note explaining that they marked this as spam
        recipient.is_supporter = False
        updated_fields.append("is_supporter")
    else:
        logger.info("User %s unsubscribed from this email: %s", recipient, event["subject"])
    recipient.unsubscribed_at = datetime.datetime.now(tz=datetime.timezone.utc)
    if persist:
        recipient.save(update_fields=updated_fields)
    return True


def process_email_rejection(
    campaign: EmailCampaign,
    recipients: Dict[str, settings.AUTH_USER_MODEL],
    events: List[SMTPEvent],
    persist=True,
) -> int:
    processed_count = 0
    spam_count, unsubscription_count = 0, 0
    for event in events:
        recipient = recipients.get(event["recipient"])
        if not recipient:
            logger.warning(
                "Unable to process this %s event: user %s was not found in our database",
                event.get("event"),
                event["recipient"],
            )
            continue
        if event["event"] in (
            SMTP2GoEventType.SOFT_BOUNCED,
            SMTP2GoEventType.HARD_BOUNCED,
            SMTP2GoEventType.REJECTED,
        ):
            logger.warning(
                "User %s could not receive an email (%s)", event["recipient"], event["event"]
            )
            # todo: add a person note to them about when this happened
            recipient.email_is_bad = True
            if persist:
                recipient.save(update_fields=["email_is_bad"])
        elif event["event"] in (SMTP2GoEventType.SPAM, SMTP2GoEventType.UNSUBSCRIBED):
            if event["event"] == SMTP2GoEventType.SPAM:
                spam_count += 1
            else:
                unsubscription_count += 1
            if not handle_spam_or_unsubscription(event, recipient=recipient, persist=persist):
                continue
        else:
            logger.error("No handler is defined for %s events", event["event"])
            continue
        processed_count += 1
    logger.info("Processed %s / %s email events", processed_count, len(events))
    if spam_count:
        campaign.spam_count += spam_count
    if unsubscription_count:
        campaign.unsubscription_count += unsubscription_count
    return spam_count, unsubscription_count


def _get_email_campaign(campaign_utm_id: str):
    from .models import EmailCampaign

    return EmailCampaign.objects.get(utm_id=campaign_utm_id)


def get_email_campaign_and_results(
    campaign_utm_id: str, event_types: List[SMTP2GoEventType], continue_token: Optional[str] = None
) -> Tuple[EmailCampaign, SMTPActivityResponse]:
    campaign = _get_email_campaign(campaign_utm_id)
    body = {
        "api_key": _api_key(),
        "limit": _ACTIVITY_BATCH_SIZE,
        "search_subject": campaign.subject,
        "event_types": [event_types],
        "continue_token": continue_token,
    }
    if continue_token:
        body["continue_token"] = continue_token

    # https://developers.smtp2go.com/reference/search-activity
    resp = requests.post(
        app_settings.SMTP2GO_API_URL + "activity/search",
        json=body,
        timeout=30,
    )
    if not resp.ok:
        logger.error("Error calling %s: %s", resp.url, resp.text)
        raise RuntimeError(f"Unable to fetch {event_types} events for {campaign}")
    return campaign, cast(SMTPActivityResponse, resp.json())


def _get_people_by_email_address_from_events(
    events: List[SMTPEvent],
) -> Dict[str, settings.AUTH_USER_MODEL]:
    email_addresses = [entry["recipient"] for entry in events]
    User = cast(settings.AUTH_USER_MODEL, get_user_model())
    return {p.email: p for p in User.objects.filter(email__in=email_addresses)}


def get_email_results_and_save_engagements(
    campaign_utm_id: str, event_types: List[SMTP2GoEventType], persist=True
) -> None:
    """
    Poll the SMTP2Go activity search endpoint for opens on this campaign,
    then record an Engagement.EMAIL_OPENED entry for each person found to have
    opened the email. Records are bulk-created and deduplicated against any
    existing entries for the same campaign, so this task is safe to run more
    than once.
    """
    continue_token = None
    new_opens, new_clicks = 0, 0
    new_spam, new_unsubscriptions = 0, 0
    while True:
        campaign, smtp_response = get_email_campaign_and_results(
            campaign_utm_id, event_types, continue_token=continue_token
        )
        events: List[SMTPEvent] = smtp_response.get("data", {}).get("events", [])
        if not events:
            if not continue_token:
                logger.info(
                    "No events of type %s found for campaign %s.", event_types, campaign_utm_id
                )
            break

        people_by_email = _get_people_by_email_address_from_events(events)
        seen_event_types = set([event["event"] for event in events]).union(set(event_types))
        if seen_event_types.union(POSITIVE_ENGAGEMENT_OUTCOMES):
            recognized_engagements, opens, clicks = process_email_engagements(
                campaign, recipients=people_by_email, events=events, persist=persist
            )
            new_opens += opens
            new_clicks += clicks
        else:
            recognized_engagements = 0

        if seen_event_types.union(BAD_OUTCOMES):
            spam, unsubscriptions = process_email_rejection(
                campaign, recipients=people_by_email, events=events, persist=persist
            )
            new_spam += spam
            new_unsubscriptions += unsubscriptions
        else:
            logger.error("No handler is defined for %s events", event_types)
        if recognized_engagements or len(events) < _ACTIVITY_BATCH_SIZE:
            # The events returned from SMTP2Go are ordered by time, recent first. Recognizing an event therefore
            # means all the events in subsequent calls shall therefore be recognized events too.
            break
    if (new_opens or new_clicks or new_spam or new_unsubscriptions) and persist:
        updated = []
        if new_opens:
            updated.append("opened_count")
        if new_clicks:
            updated.append("clicked_count")
        if new_spam:
            updated.append("spam_count")
        if new_unsubscriptions:
            updated.append("unsubscription_count")
        campaign.save(update_fields=updated)


def register_email_failure_webhooks() -> None:
    """
    Register a webhook with SMTP2Go to receive delivery-failure notifications for
    the four problem event types: spam, bounce, reject, and unsubscribe.

    The webhook URL is derived from WAGTAILADMIN_BASE_URL, which must be set and
    publicly reachable from SMTP2Go's servers. Run this command once per
    deployment; re-running it will create a duplicate webhook entry on SMTP2Go's
    side, so check the existing webhooks first if you are unsure.
    """
    from urllib.parse import urlparse

    from django.conf import settings
    from django.urls import reverse

    webhook_url = settings.WAGTAILADMIN_BASE_URL.rstrip("/") + reverse("email_webhook")

    hostname = urlparse(webhook_url).hostname or ""
    if hostname == "localhost" or hostname.startswith("127.") or hostname == "::1":
        raise ValueError(
            f"WAGTAILADMIN_BASE_URL resolves to a local address ({hostname!r}). "
            "SMTP2Go cannot reach a localhost URL — set WAGTAILADMIN_BASE_URL to the "
            "publicly accessible domain before registering webhooks."
        )

    logger.info("Registering SMTP2Go webhook at %s.", webhook_url)

    resp = requests.post(
        app_settings.SMTP2GO_API_URL + "webhook/add",
        json={
            "api_key": _api_key(),
            "url": webhook_url,
            "events": ["spam", "bounce", "reject", "unsubscribe"],
            "output_format": "json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    logger.info("SMTP2Go webhook registration response: %s", resp.json())
