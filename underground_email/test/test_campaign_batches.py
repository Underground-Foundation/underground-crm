"""
Tests for how send_emails divides a campaign's recipients into send_email_batch tasks.

Only the broker and SMTP2Go are stubbed, so the assertions are against the task packages
that would actually have been handed to Redis.
"""

import os
from typing import List
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django_q.signing import SignedPackage
from wagtail.models import Site

from underground_crm.models import PeopleFilter
from underground_email.models import EmailCampaign, EmailSender
from underground_email.tasks import (
    CAMPAIGN_BATCH_TIMEOUT_SECONDS,
    send_email_batch,
    send_emails,
)
from underground_email.test.test_campaign_site import _accepting_session

RECIPIENT_COUNT = 5
BATCH_SIZE = 2


class CampaignBatchTests(TestCase):
    def setUp(self) -> None:
        self.recipients = [
            get_user_model().objects.create_user(
                email=f"volunteer.{number}@example.org",
                first_name="Volunteer",
                last_name=str(number),
                email_opt_in=True,
            )
            for number in range(RECIPIENT_COUNT)
        ]
        secretary = get_user_model().objects.create_user(
            email="secretary@fusionparty.org.au", first_name="Sam", last_name="Okafor"
        )
        self.campaign = EmailCampaign.objects.create(
            subject="Volunteers needed in Brunswick this weekend",
            sender=EmailSender.objects.create(sender=secretary),
            site=Site.objects.get(is_default_site=True),
            template="underground_email/smtp2go_base.html",
            people_filter=PeopleFilter.objects.create(
                name="Everyone", criteria={"logic": "AND", "rules": []}
            ),
            state=1,
        )

    def _queue_batches(self) -> List[dict]:
        """Run send_emails with batches of BATCH_SIZE, and return the task packages
        that it queued."""
        packages = []

        def fake_enqueue(pack):
            packages.append(SignedPackage.loads(pack))
            return "fake-task-id"

        broker = MagicMock()
        broker.enqueue.side_effect = fake_enqueue
        broker.cache = None

        with (
            patch("django_q.tasks.get_broker", return_value=broker),
            patch("underground_email.tasks.CAMPAIGN_BATCH_SIZE", BATCH_SIZE),
        ):
            send_emails(self.campaign.utm_id)
        return packages

    def test_recipients_are_divided_into_contiguous_ranges_of_ids(self) -> None:
        """
        The ranges are ordered by ID, and each one ends where the next begins, with the
        first and last left open, so that every possible ID falls into exactly one.
        """
        packages = self._queue_batches()

        ranges = [package["args"][1:] for package in packages]
        ordered_ids = sorted(str(person.pk) for person in self.recipients)
        self.assertEqual(
            ranges,
            [(None, ordered_ids[2]), (ordered_ids[2], ordered_ids[4]), (ordered_ids[4], None)],
        )

    def test_each_batch_carries_the_longer_timeout(self) -> None:
        """
        django-q2 pops "timeout" out of the keyword arguments and treats it as a task
        option. If that ever stopped being true it would reach send_email_batch() as an
        unexpected keyword argument and every batch would fail immediately.
        """
        packages = self._queue_batches()

        for package in packages:
            self.assertEqual(package["func"], send_email_batch)
            self.assertEqual(package["timeout"], CAMPAIGN_BATCH_TIMEOUT_SECONDS)
            self.assertEqual(package["args"][0], self.campaign.utm_id)
            self.assertNotIn("timeout", package.get("kwargs", {}))

    @patch.dict(os.environ, {"SMTP_KEY": "api-1234567890"})
    def test_running_every_batch_sends_to_each_recipient_once(self) -> None:
        packages = self._queue_batches()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.state, 2)
        self.assertEqual(self.campaign.sent_count, 0, "nothing is sent until a batch runs")

        with patch("underground_email.tasks.requests.Session") as session_class:
            session = _accepting_session(session_class)
            for package in packages:
                send_email_batch(*package["args"])

        sent_to = sorted(call.kwargs["json"]["to"][0] for call in session.post.call_args_list)
        self.assertEqual(sent_to, sorted(f"{p.full_name} <{p.email}>" for p in self.recipients))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.sent_count, RECIPIENT_COUNT)

    def test_campaign_without_recipients_queues_nothing(self) -> None:
        get_user_model().objects.filter(email__startswith="volunteer.").update(email_opt_in=False)

        packages = self._queue_batches()

        self.assertEqual(packages, [])
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.state, 2)
