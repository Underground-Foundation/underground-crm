import logging
import os
import datetime
from typing import TYPE_CHECKING, Dict, cast, List, Union, Tuple, Optional

import requests
from cachetools import cached
from django.conf import settings
from django.contrib.auth import get_user_model
from django.template import Context, Template as DjangoTemplate
from django.template.loader import render_to_string
from django.utils import timezone
from wagtail.models import Site

from underground_email import app_settings
from underground_email.backends import SEND_TIMEOUT_SECONDS
from underground_email.api import (
    SMTP2GoEventType,
    BAD_OUTCOMES,
    SMTPActivityResponse,
    SMTPEvent,
    WebhookEmailDict,
    POSITIVE_ENGAGEMENT_OUTCOMES,
)
from underground_email.models import EmailCampaign
from underground_email.unsubscription import make_unsubscription_url, subscription_page_url

if TYPE_CHECKING:
    from django_q.models import Schedule

logger = logging.getLogger(__name__)
_ACTIVITY_BATCH_SIZE = 1_000  # https://developers.SMTP2Go.com/reference/activity-search

# How many recipients send_emails gives each send_email_batch task.
CAMPAIGN_BATCH_SIZE = 500

# Overrides Q_CLUSTER["timeout"] for send_email_batch only, which sends one SMTP2Go request
# per recipient: at a few hundred milliseconds each, a batch of CAMPAIGN_BATCH_SIZE runs
# for longer than the cluster's two-minute default.
#
# Being killed part-way through is not merely a retry away, because send_email_batch is
# not idempotent: some of the batch's recipients already have the email, and re-running
# the batch would send it to them again. That makes a generous ceiling the safer failure
# mode; its purpose is only to stop a wedged batch occupying a worker forever.
CAMPAIGN_BATCH_TIMEOUT_SECONDS = 1_800

# How long after sending a campaign we wait before collecting its results from SMTP2Go,
# by which time most of the recipients who will ever open it have done so.
EMAIL_RESULTS_DELAY = datetime.timedelta(hours=24)


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
                "preferred_or_first_name": person.preferred_name or person.first_name,
                "first_name": person.first_name or "",
                "last_name": person.last_name or "",
                "full_name": person.full_name,
            }
        )
    )


def _render_email_html(
    campaign: EmailCampaign, person, unsubscription_page_url: Optional[str] = None
) -> str:
    """Render the campaign's email template for a specific recipient.

    unsubscription_page_url is the URL of the campaign site's SubscriptionPage,
    which send_emails looks up once for all its recipients; it is looked up
    here if not given.
    """
    root_url = campaign.site.root_url.rstrip("/")
    if unsubscription_page_url is None:
        unsubscription_page_url = subscription_page_url(campaign.site)

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
            "unsubscription_url": make_unsubscription_url(
                person, campaign.utm_id, unsubscription_page_url
            ),
            "recipient_email": person.email,
            "site_url": root_url,
        },
    )


def send_emails(campaign_utm_id: str) -> None:
    """
    Divide a campaign's eligible recipients into batches of CAMPAIGN_BATCH_SIZE, ordered
    by their IDs, and queue a send_email_batch task for each batch. The campaign is then
    marked as sent and get_email_results is scheduled (see schedule_email_results); the
    batches run afterwards, in parallel on as many of the cluster's workers as are free,
    and each adds the emails that it sent to the campaign's sent_count.

    Each batch is given the range of IDs that it covers rather than a list of people, and
    the ranges are contiguous: the first is open at its start and the last at its end. A
    person who becomes eligible between this task and their batch is therefore sent the
    email by whichever batch's range contains their ID, and a person who stops being
    eligible is not sent it.

    A batch cannot be resumed. Nothing records which recipients have already been sent
    the campaign, so a batch that is interrupted part-way has sent the email to only some
    of its recipients, and running it again would send the email to all of them again
    (see CAMPAIGN_BATCH_TIMEOUT_SECONDS). The workaround is to choose different
    recipients: find the people in that batch who were not sent the email (from SMTP2Go's
    activity search, or from the "sent to" lines that the batch logs), give the campaign
    a people filter that selects only them, and run send_email_batch again for the
    batch's range.

    Notice that this function is invoked through ScheduleEmailCampaignAction in
    wagtail_hooks.py
    """
    from django_q.tasks import async_task

    from .models import EmailCampaign

    logger.info("send_emails started for campaign %s.", campaign_utm_id)

    campaign = EmailCampaign.objects.select_related("people_filter", "site").get(
        utm_id=campaign_utm_id
    )

    recipients_qs = campaign.get_potential_recipients()
    recipient_ids = (
        []
        if recipients_qs is None
        else [str(pk) for pk in recipients_qs.order_by("id").values_list("id", flat=True)]
    )
    logger.info(
        "Campaign %s (%r): %s eligible recipient(s) found.",
        campaign_utm_id,
        campaign.subject,
        len(recipient_ids) if recipients_qs is not None else "no filter set — 0",
    )
    if not recipient_ids:
        logger.warning("Campaign %s has no eligible recipients; marking as sent.", campaign_utm_id)
        campaign.state = 2
        campaign.save(update_fields=["state"])
        return

    # Looked up before anything is queued, so that a site without a subscription page
    # stops the campaign rather than sending emails whose unsubscription links lead
    # nowhere.
    subscription_page_url(campaign.site)

    # The first ID of each batch, which is also where the batch before it ends.
    boundaries = recipient_ids[CAMPAIGN_BATCH_SIZE::CAMPAIGN_BATCH_SIZE]
    ranges = list(zip([None, *boundaries], [*boundaries, None]))

    # Written before any batch is queued, because each batch adds to it.
    campaign.sent_count = 0
    campaign.save(update_fields=["sent_count"])
    for number, (start_id, end_id) in enumerate(ranges, start=1):
        async_task(
            send_email_batch,
            campaign_utm_id,
            start_id,
            end_id,
            task_name=f"email_campaign_{campaign_utm_id}_batch_{number}",
            timeout=CAMPAIGN_BATCH_TIMEOUT_SECONDS,
        )
    logger.info(
        "Campaign %s: queued %d batch(es) of up to %d recipients.",
        campaign_utm_id,
        len(ranges),
        CAMPAIGN_BATCH_SIZE,
    )

    campaign.state = 2
    updated = ["state"]
    if not campaign.sending_date:
        campaign.sending_date = datetime.datetime.now(tz=datetime.timezone.utc)
        updated.append("sending_date")
    campaign.save(update_fields=updated)

    schedule_email_results(campaign_utm_id)


def send_email_batch(campaign_utm_id: str, start_id: Optional[str], end_id: Optional[str]) -> None:
    """
    Send a campaign to those of its eligible recipients whose IDs are at least start_id
    and less than end_id, with one request to SMTP2Go's email/send endpoint per recipient
    so that every email is personalised, and add the number sent to the campaign's
    sent_count. A start_id or end_id of None leaves the range open at that end.

    Every request goes through one requests.Session, so that the connection to SMTP2Go
    is kept alive across recipients rather than being established anew for each of them.

    send_emails queues one of these tasks per batch; see its documentation for how the
    ranges are chosen, and for what to do when a batch is interrupted.
    """
    from django.db.models import F, Value
    from django.db.models.functions import Coalesce

    from .models import EmailCampaign

    campaign = EmailCampaign.objects.select_related(
        "sender__sender", "greeting", "people_filter", "site"
    ).get(utm_id=campaign_utm_id)

    recipients_qs = campaign.get_potential_recipients()
    if recipients_qs is None:
        raise RuntimeError(f"Campaign {campaign_utm_id} no longer has a people filter.")
    if start_id is not None:
        recipients_qs = recipients_qs.filter(id__gte=start_id)
    if end_id is not None:
        recipients_qs = recipients_qs.filter(id__lt=end_id)
    recipients = list(
        recipients_qs.order_by("id").only(
            "id", "email", "preferred_name", "first_name", "last_name"
        )
    )
    logger.info(
        "Campaign %s: sending the batch from %s to %s, of %d recipient(s).",
        campaign_utm_id,
        start_id or "the first recipient",
        end_id or "the last recipient",
        len(recipients),
    )

    sender_person = campaign.sender.sender
    sender_str = f"{sender_person.full_name} <{sender_person.email}>"
    unsubscription_page_url = subscription_page_url(campaign.site)
    sent_count = 0

    with requests.Session() as session:
        session.headers["X-Smtp2go-Api-Key"] = _api_key()
        for person in recipients:
            try:
                # https://developers.smtp2go.com/reference/send-standard-email
                response = session.post(
                    app_settings.SMTP2GO_API_URL + "email/send",
                    json={
                        "sender": sender_str,
                        "to": [f"{person.full_name} <{person.email}>"],
                        "subject": campaign.subject,
                        "html_body": _render_email_html(campaign, person, unsubscription_page_url),
                    },
                    timeout=SEND_TIMEOUT_SECONDS,
                )
                response_json = response.json()
                data = response_json.get("data", {})
                if response.ok and data.get("succeeded") and not data.get("failed"):
                    sent_count += 1
                    logger.info(
                        "Campaign %s: sent to %s (request %s).",
                        campaign_utm_id,
                        person.pk,
                        response_json.get("request_id"),
                    )
                else:
                    logger.error(
                        "Campaign %s: failed to send to %s (HTTP %s, request %s): %s",
                        campaign_utm_id,
                        person.email,
                        response.status_code,
                        response_json.get("request_id"),
                        data.get("failures") or data.get("error"),
                    )
            except Exception:
                logger.exception(
                    "Campaign %s: exception sending to %s.",
                    campaign_utm_id,
                    person.email,
                )

    # Added in the database rather than read, added to and saved, because the
    # campaign's other batches are doing the same at the same time.
    EmailCampaign.objects.filter(pk=campaign.pk).update(
        sent_count=Coalesce(F("sent_count"), Value(0)) + sent_count
    )
    logger.info(
        "Campaign %s: the batch from %s to %s sent %d of %d email(s).",
        campaign_utm_id,
        start_id or "the first recipient",
        end_id or "the last recipient",
        sent_count,
        len(recipients),
    )


def schedule_email_results(
    campaign_utm_id: str, now: Optional[datetime.datetime] = None
) -> "Schedule":
    """Schedule get_email_results to update a campaign's statistics once
    EMAIL_RESULTS_DELAY has passed since it was sent."""
    from django_q.models import Schedule
    from django_q.tasks import schedule as q_schedule

    return q_schedule(
        # Derived from the function itself rather than spelt out, because django-q only
        # resolves the name when the schedule fires, a day later: a misspelt name used to
        # fail then, quietly, and the campaign's statistics were never updated.
        f"{get_email_results.__module__}.{get_email_results.__qualname__}",
        campaign_utm_id,
        # This names the task that the schedule starts, not the schedule itself.
        task_name=f"email_results_{campaign_utm_id}",
        schedule_type=Schedule.ONCE,
        next_run=(now or timezone.now()) + EMAIL_RESULTS_DELAY,
    )


# Every kind of event whose results are recorded against a campaign. SMTP2Go's activity
# search is queried for one type at a time, so that each query pages independently.
PROCESSABLE_EVENT_TYPES: Tuple[SMTP2GoEventType, ...] = (
    SMTP2GoEventType.OPENED,
    SMTP2GoEventType.CLICKED,
    SMTP2GoEventType.SOFT_BOUNCED,
    SMTP2GoEventType.HARD_BOUNCED,
    SMTP2GoEventType.REJECTED,
    SMTP2GoEventType.SPAM,
    SMTP2GoEventType.UNSUBSCRIBED,
)


def get_email_results(campaign_utm_id: str) -> None:
    """
    The task that send_emails schedules for 24 hours after a campaign has been sent,
    which updates the campaign's statistics from SMTP2Go. Each type of event is
    processed independently, so that a failure fetching one (an SMTP2Go outage, say)
    does not discard the others; the task still fails afterwards, so that django-q
    records the failure rather than reporting success.
    """
    failed_event_types = []
    for event_type in PROCESSABLE_EVENT_TYPES:
        try:
            get_email_results_and_save_engagements(campaign_utm_id, event_type)
        except Exception:
            logger.exception(
                "Campaign %s: unable to process %s events.", campaign_utm_id, event_type
            )
            failed_event_types.append(event_type)
    if failed_event_types:
        raise RuntimeError(
            f"Campaign {campaign_utm_id}: unable to process "
            f"{', '.join(failed_event_types)} events."
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
        newly_engaged: set = set()
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
            elif person.pk not in newly_engaged:
                # A person who opens an email several times produces several events in
                # the same batch, but let's only save it as one engagement.
                newly_engaged.add(person.pk)
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
    updated_fields = ["email_opt_in", "unsubscribed_at"]
    if event.get("event") == SMTP2GoEventType.SPAM:
        # This act will make emails harder to deliver to everyone else.
        logger.warning("User %s 🗡 marked this email as spam: %s", recipient, event.get("subject"))
        # todo: add a person note explaining that they marked this as spam
        recipient.is_supporter = False
        updated_fields.append("is_supporter")
    else:
        logger.info("User %s unsubscribed from this email: %s", recipient, event.get("subject"))
    recipient.email_opt_in = False
    recipient.unsubscribed_at = datetime.datetime.now(tz=datetime.timezone.utc)
    if persist:
        recipient.save(update_fields=updated_fields)
    return True


def process_email_rejection(
    campaign: EmailCampaign,
    recipients: Dict[str, settings.AUTH_USER_MODEL],
    events: List[SMTPEvent],
    persist=True,
) -> Tuple[int, int]:
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
        if event["event"] == SMTP2GoEventType.SOFT_BOUNCED:
            # A soft bounce is a temporary failure, such as a full mailbox, so it says
            # nothing about whether the address can receive emails in general.
            logger.info(
                "User %s temporarily could not receive an email (%s)",
                event["recipient"],
                event["event"],
            )
        elif event["event"] in (SMTP2GoEventType.HARD_BOUNCED, SMTP2GoEventType.REJECTED):
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
    # Both counts are null until a campaign's results have first been collected.
    if spam_count:
        campaign.spam_count = (campaign.spam_count or 0) + spam_count
    if unsubscription_count:
        campaign.unsubscription_count = (campaign.unsubscription_count or 0) + unsubscription_count
    return spam_count, unsubscription_count


def _get_email_campaign(campaign_utm_id: str):
    from .models import EmailCampaign

    return EmailCampaign.objects.get(utm_id=campaign_utm_id)


def get_email_campaign_and_results(
    campaign_utm_id: str, event_type: SMTP2GoEventType, continue_token: Optional[str] = None
) -> Tuple[EmailCampaign, SMTPActivityResponse]:
    campaign = _get_email_campaign(campaign_utm_id)
    body = {
        "api_key": _api_key(),
        "limit": _ACTIVITY_BATCH_SIZE,
        "search_subject": campaign.subject,
        "event_types": [event_type],
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
        raise RuntimeError(f"Unable to fetch {event_type} events for {campaign}")
    return campaign, cast(SMTPActivityResponse, resp.json())


def _get_people_by_email_address_from_events(
    events: List[SMTPEvent],
) -> Dict[str, settings.AUTH_USER_MODEL]:
    email_addresses = [entry["recipient"] for entry in events]
    User = cast(settings.AUTH_USER_MODEL, get_user_model())
    return {p.email: p for p in User.objects.filter(email__in=email_addresses)}


def get_email_results_and_save_engagements(
    campaign_utm_id: str, event_type: SMTP2GoEventType, persist=True
) -> None:
    """
    Poll the SMTP2Go activity search endpoint for one type of event on this campaign,
    and record what it finds: an Engagement for each person who opened or clicked, and
    the consequences of a bounce, rejection, spam report or unsubscription. Engagements
    are deduplicated against any existing entries for the same campaign, so this task
    is safe to run more than once.
    """
    continue_token = None
    new_opens, new_clicks = 0, 0
    new_spam, new_unsubscriptions = 0, 0
    while True:
        campaign, smtp_response = get_email_campaign_and_results(
            campaign_utm_id, event_type, continue_token=continue_token
        )
        data = smtp_response.get("data", {})
        events: List[SMTPEvent] = data.get("events", [])
        if not events:
            if not continue_token:
                logger.info(
                    "No events of type %s found for campaign %s.", event_type, campaign_utm_id
                )
            break

        people_by_email = _get_people_by_email_address_from_events(events)
        seen_event_types = {event["event"] for event in events}
        if seen_event_types & POSITIVE_ENGAGEMENT_OUTCOMES:
            recognized_engagements, opens, clicks = process_email_engagements(
                campaign, recipients=people_by_email, events=events, persist=persist
            )
            new_opens += opens
            new_clicks += clicks
        else:
            recognized_engagements = 0

        if seen_event_types & BAD_OUTCOMES:
            spam, unsubscriptions = process_email_rejection(
                campaign, recipients=people_by_email, events=events, persist=persist
            )
            new_spam += spam
            new_unsubscriptions += unsubscriptions
        if recognized_engagements or len(events) < _ACTIVITY_BATCH_SIZE:
            # The events returned from SMTP2Go are ordered by time, recent first. Recognizing an event therefore
            # means all the events in subsequent calls shall therefore be recognized events too.
            break
        continue_token = data.get("continue_token")
        if not continue_token:
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


def get_smtp2go_key() -> str:
    """The shared secret that SMTP2Go presents when it delivers a webhook. This is
    deliberately not the API key (see _api_key): it is sent in every delivery, and
    somebody who intercepted it should not gain the ability to call SMTP2Go's API."""
    key = settings.SMTP2GO_WEBHOOK_SECRET
    if not key:
        raise RuntimeError("SMTP2GO_WEBHOOK_SECRET environment variable is not set")
    return key


def register_email_failure_webhooks(site: Site) -> None:
    """
    Register a webhook with SMTP2Go to receive delivery-failure notifications for
    the four problem event types: spam, bounce, reject, and unsubscribe.

    The webhook URL is derived from the root URL of the given site, which must be
    publicly reachable from SMTP2Go's servers. Run this command once per
    deployment; re-running it will create a duplicate webhook entry on SMTP2Go's
    side, so check the existing webhooks first if you are unsure.

    https://support.smtp2go.com/hc/en-gb/articles/223087967-Webhooks
    """
    from urllib.parse import urlparse

    from django.urls import reverse

    webhook_url = site.root_url.rstrip("/") + reverse("email_webhook")

    hostname = urlparse(webhook_url).hostname or ""
    if hostname == "localhost" or hostname.startswith("127.") or hostname == "::1":
        raise ValueError(
            f"The site's root URL resolves to a local address ({hostname!r}). "
            "SMTP2Go cannot reach a localhost URL — choose a site with a publicly "
            "accessible domain, or set this site's hostname to one, before registering webhooks."
        )

    logger.info("Registering SMTP2Go webhook at %s.", webhook_url)

    resp = requests.post(
        app_settings.SMTP2GO_API_URL + "webhook/add",
        json={
            "api_key": _api_key(),
            "url": webhook_url,
            "events": ["spam", "bounce", "reject", "unsubscribe"],
            "output_format": "json",
            "auth_header_type": "bearer",
            "auth_header_value": get_smtp2go_key(),
        },
        timeout=30,
    )
    resp.raise_for_status()
    logger.info("SMTP2Go webhook registration response: %s", resp.json())
