import logging
import os
from datetime import timedelta
from typing import Any, Dict

import requests
from cachetools import cached
from django.conf import settings
from django.contrib.auth import get_user_model
from django.template import Context, Template as DjangoTemplate
from django.template.loader import render_to_string
from django.utils import timezone
from openpyxl.pivot import record
from smtp2go.core import Smtp2goClient
from underground_email import app_settings
from underground_email.api import SMTP2GoEventType
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
        next_run=timezone.now() + timedelta(hours=24),
    )


def _handle_spam(recipient: str, payload: dict[str, Any]) -> None:
    # TODO: mark the person as having reported spam (e.g. set a flag or add a tag).
    logger.info("Spam report from %s.", recipient)


def _handle_bounce(recipient: str, payload: dict[str, Any]) -> None:
    # TODO: mark the email address as bounced so it is excluded from future sends.
    logger.info("Bounce for %s.", recipient)


def _handle_reject(recipient: str, payload: dict[str, Any]) -> None:
    # TODO: handle a rejection (e.g. log for review; may indicate a suppression-list hit).
    logger.info("Rejection for %s.", recipient)


def _handle_unsubscription(recipient: str, payload: dict[str, Any]) -> None:
    # TODO: record the unsubscription via unsubscribe_from_tag_via_email_campaign or
    # by setting person.unsubscribed_at directly if no campaign context is available.
    logger.info("Unsubscription for %s.", recipient)


def process_email_engagement(
    campaign: EmailCampaign, recipients: Dict[str, settings.USER_AUTH_MODEL], events
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
    return


def get_email_results_and_save_engagements(campaign_utm_id: str, event_type="opened") -> None:
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
    data = resp.json()

    events = data.get("data", {}).get("emails", [])
    if not events:
        logger.info("No events of type %s found for campaign %s.", event_type, campaign_utm_id)
        return

    email_addresses = [entry["recipient"] for entry in events]
    User = get_user_model()
    people_by_email: dict[str, User] = {
        p.email: p for p in User.objects.filter(email__in=email_addresses)
    }
    if event_type in (SMTP2GoEventType.OPENED, SMTP2GoEventType.CLICKED):
        process_email_engagement(campaign, recipients=people_by_email, events=events)
    campaign.save(update_fields=["opened_count"])


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
