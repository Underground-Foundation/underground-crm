"""
Tests for collecting a campaign's results from SMTP2Go once it has been sent.

send_emails schedules get_email_results for a day after a campaign goes out. That
schedule used to name a function that did not exist, so django-q failed to run it and
no campaign's statistics were ever updated in production.
"""

import datetime
import pydoc

import django.test
from django.contrib.auth import get_user_model
from django.utils import timezone
from django_q.models import Schedule

from underground_crm.models import Engagement
from underground_email.api import SMTP2GoEventType, SMTPEvent
from underground_email.models import EmailCampaign, generate_base64_id
from underground_email.tasks import (
    EMAIL_RESULTS_DELAY,
    get_email_results,
    process_email_engagements,
    schedule_email_results,
)

Person = get_user_model()


class EmailResultsScheduleTest(django.test.TestCase):
    def test_the_schedule_names_a_function_that_django_q_can_run(self):
        campaign_utm_id = generate_base64_id()
        sent_at = timezone.make_aware(datetime.datetime(2026, 11, 18, 19, 30))

        schedule = Schedule.objects.get(pk=schedule_email_results(campaign_utm_id, now=sent_at).pk)
        # django-q's worker resolves the stored name with pydoc.locate when the schedule
        # fires, and fails the task if that finds nothing.
        self.assertIs(
            pydoc.locate(schedule.func),
            get_email_results,
            f"django-q would not find {schedule.func!r} when the schedule fires.",
        )
        self.assertEqual(schedule.next_run, sent_at + EMAIL_RESULTS_DELAY)


class EmailEngagementTest(django.test.TestCase):
    def setUp(self) -> None:
        # process_email_engagements leaves saving the campaign to its caller.
        self.campaign = EmailCampaign(
            utm_id=generate_base64_id(),
            subject="Join us at the Footscray street stall on Saturday",
            preview_text="We need a few more hands to hand out flyers.",
        )
        self.readers = [
            Person.objects.create_user(email=email, password="correct horse battery staple")
            for email in ("priya.raman@example.com", "liam.oconnor@example.com")
        ]

    def test_each_reader_is_one_engagement_however_often_they_open_it(self):
        """Mail clients often report several opens by the same person. Each person
        should still be recorded, and counted, once."""
        opens_per_reader = 3
        events: list[SMTPEvent] = [
            {"recipient": reader.email, "event": SMTP2GoEventType.OPENED}
            for reader in self.readers
            for _ in range(opens_per_reader)
        ]

        _, new_opens, new_clicks = process_email_engagements(
            self.campaign,
            recipients={reader.email: reader for reader in self.readers},
            events=events,
        )

        self.assertEqual(new_opens, len(self.readers))
        self.assertEqual(new_clicks, 0, "Only open events were supplied.")
        self.assertEqual(
            set(
                Engagement.objects.filter(
                    action_type=Engagement.EMAIL_OPENED,
                    metadata__campaign_utm_id=self.campaign.utm_id,
                ).values_list("person_id", flat=True)
            ),
            {reader.pk for reader in self.readers},
        )
        self.assertEqual(
            Engagement.objects.filter(metadata__campaign_utm_id=self.campaign.utm_id).count(),
            len(self.readers),
            "Repeated opens must not produce duplicate engagements.",
        )
