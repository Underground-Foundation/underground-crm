import logging
import os
import datetime
from typing import Any, Dict, cast, List, Union

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
            "subject": campaign.title,
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
        campaign.title,
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
                subject=campaign.title,
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
    campaign.save(update_fields=["state", "sent_count"])
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


def process_email_engagement(
    campaign: EmailCampaign,
    recipients: Dict[str, settings.AUTH_USER_MODEL],
    events: List[SMTPEvent],
    persist=True,
) -> int:
    from underground_crm.models import Engagement

    # Exclude people who already have an engagement for this campaign.
    for event_type in SMTP2GoEventType.OPENED, SMTP2GoEventType.CLICKED:
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

        new_engagements = [
            Engagement(
                person=person,
                action_type=action_type,
                page_title=campaign.title,
                metadata={"campaign_utm_id": campaign.utm_id},
            )
            for entry in events
            if (person := recipients.get(entry["recipient"]))
            and person.pk not in already_engaged
            and entry["event"] == event_type.value
        ]

        if new_engagements:
            if persist:
                Engagement.objects.bulk_create(new_engagements)
            logger.info(
                "Recorded %d %s engagements for campaign %s.",
                len(new_engagements),
                event_type,
                campaign.utm_id,
            )

        if action_type == Engagement.EMAIL_OPENED:
            campaign.opened_count = (campaign.opened_count or 0) + len(new_engagements)
        else:
            campaign.clicked_count = (campaign.clicked_count or 0) + len(new_engagements)
    # todo: I want to send a signal that we've already seen some events. Dropping some from processing might be
    # because the person could not be found.
    return len(new_engagements)


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
    for event in events:
        recipient = recipients.get(event["recipient"])
        if not recipient:
            logger.warning(
                "Unable to process this %s event: user %s was not found in our database",
                event.get("event"),
                event["recipient"],
            )
            continue
        if event.get("event") in (
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
        elif event.get("event") in (SMTP2GoEventType.SPAM, SMTP2GoEventType.UNSUBSCRIBED):
            if not handle_spam_or_unsubscription(event, recipient=recipient, persist=persist):
                continue
        else:
            logger.error("No handler is defined for %s events", event["event"])
            continue
        processed_count += 1
    logger.info("Processed %s / %s email events", processed_count, len(events))
    return processed_count


def get_email_results_and_save_engagements(
    campaign_utm_id: str, event_type: SMTP2GoEventType, persist=True
) -> None:
    """
    Poll the SMTP2Go activity search endpoint for opens on this campaign,
    then record an Engagement.EMAIL_OPENED entry for each person found to have
    opened the email. Records are bulk-created and deduplicated against any
    existing entries for the same campaign, so this task is safe to run more
    than once.
    """
    from .models import EmailCampaign

    campaign = EmailCampaign.objects.get(utm_id=campaign_utm_id)
    api_key = _api_key()

    # https://developers.smtp2go.com/reference/search-activity
    resp = requests.post(
        app_settings.SMTP2GO_API_URL + "activity/search",
        json={
            "api_key": api_key,
            "limit": _ACTIVITY_BATCH_SIZE,
            "search_subject": campaign.title,
            "events": [event_type],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = cast(SMTPActivityResponse, resp.json())
    events: List[SMTPEvent] = data.get("data", {}).get("events", [])
    if not events:
        logger.info("No events of type %s found for campaign %s.", event_type, campaign_utm_id)
        return

    email_addresses = [entry["recipient"] for entry in events]
    User = get_user_model()
    people_by_email: dict[str, User] = {
        p.email: p for p in User.objects.filter(email__in=email_addresses)
    }
    if event_type in (SMTP2GoEventType.OPENED, SMTP2GoEventType.CLICKED):
        processed = process_email_engagement(
            campaign, recipients=people_by_email, events=events, persist=persist
        )
        # todo: avoid calling again if we already saw some of the processed events
        campaign.save(update_fields=["opened_count"])
        pass
    elif event_type in BAD_OUTCOMES:
        processed = process_email_rejection(
            campaign, recipients=people_by_email, events=events, persist=persist
        )
    else:
        logger.error("No handler is defined for %s events", event_type)


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
