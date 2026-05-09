import logging
import os
from datetime import timedelta

import requests
from cachetools import cached
from django.template import Context, Template as DjangoTemplate
from django.template.loader import render_to_string
from django.utils import timezone
from smtp2go.core import Smtp2goClient
from underground_email import app_settings
from underground_email.models import EmailCampaign
from underground_email.unsubscription import make_unsubscription_url

logger = logging.getLogger(__name__)
_ACTIVITY_BATCH_SIZE = 1_000  # https://developers.smtp2go.com/reference/activity-search


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
    Upload the campaign template to smtp2go and dispatch personalised emails to
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

    # The smtp2go client library only wraps the single-send endpoint, so we
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


def get_email_results(campaign_utm_id: str) -> None:
    """
    Poll the smtp2go activity search endpoint for opens on this campaign,
    then record an Engagement.EMAIL_OPENED entry for each person found to have
    opened the email. Records are bulk-created and deduplicated against any
    existing entries for the same campaign, so this task is safe to run more
    than once.
    """
    from .models import EmailCampaign
    from underground_crm.models import Engagement, Person

    campaign = EmailCampaign.objects.get(utm_id=campaign_utm_id)
    api_key = _api_key()

    resp = requests.post(
        app_settings.SMTP2GO_API_URL + "activity/search",
        json={
            "api_key": api_key,
            "limit": _ACTIVITY_BATCH_SIZE,
            "search_subject": campaign.title,
            "events": ["opened"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    opened_entries = data.get("data", {}).get("emails", [])
    if not opened_entries:
        logger.info("No opens found for campaign %s.", campaign_utm_id)
        return

    email_addresses = [entry["recipient"] for entry in opened_entries]
    persons_by_email: dict[str, Person] = {
        p.email: p for p in Person.objects.filter(email__in=email_addresses)
    }

    # Exclude persons who already have an EMAIL_OPENED engagement for this campaign.
    already_recorded: set = set(
        Engagement.objects.filter(
            action_type=Engagement.EMAIL_OPENED,
            metadata__campaign_utm_id=campaign_utm_id,
        ).values_list("person_id", flat=True)
    )

    new_engagements = [
        Engagement(
            person=person,
            action_type=Engagement.EMAIL_OPENED,
            page_title=campaign.title,
            metadata={"campaign_utm_id": campaign_utm_id},
        )
        for entry in opened_entries
        if (person := persons_by_email.get(entry["recipient"]))
        and person.pk not in already_recorded
    ]

    if new_engagements:
        Engagement.objects.bulk_create(new_engagements)
        logger.info(
            "Recorded %d EMAIL_OPENED engagements for campaign %s.",
            len(new_engagements),
            campaign_utm_id,
        )

    campaign.opened_count = (campaign.opened_count or 0) + len(new_engagements)
    campaign.save(update_fields=["opened_count"])


def register_email_failure_webhook():
    """
    todo: register the webhook for bad email results:
    * unsubscribe
    * spam
    * reject
    """
    pass
