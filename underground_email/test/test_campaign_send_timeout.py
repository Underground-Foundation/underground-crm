"""
Tests for the queued-task timeouts that apply to campaign sends.

Q_CLUSTER sets a two-minute ceiling so a wedged task cannot occupy a worker forever.
send_emails only divides a campaign's recipients into batches, so it runs within that
ceiling, but each send_email_batch it queues issues one SMTP2Go request per recipient and
legitimately runs for longer (see CAMPAIGN_BATCH_TIMEOUT_SECONDS). The batches' own
timeout is tested in test_campaign_batches.py.
"""

import ast
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from django_q.conf import Conf
from django_q.models import Schedule
from django_q.signing import SignedPackage

from underground_email.tasks import CAMPAIGN_BATCH_TIMEOUT_SECONDS
from underground_email.wagtail_hooks import ScheduleEmailCampaignAction


class ClusterTimeoutSettingsTests(SimpleTestCase):
    def test_tasks_are_capped_at_two_minutes(self):
        self.assertEqual(Conf.TIMEOUT, 120)

    def test_timeout_and_retry_keep_the_relationship_django_q_validates(self):
        """
        django-q2 warns at startup unless a timeout is set and is no larger than retry.
        The warning is emitted by every process that loads the app registry, so getting
        this wrong means a continuous stream of identical complaints in the logs.
        """
        self.assertTrue(Conf.TIMEOUT)
        self.assertLessEqual(Conf.TIMEOUT, Conf.RETRY)

    def test_campaign_batches_are_given_room_beyond_the_cluster_ceiling(self):
        self.assertGreater(CAMPAIGN_BATCH_TIMEOUT_SECONDS, Conf.TIMEOUT)


class CampaignSendEnqueueTests(TestCase):
    """
    Exercises the real enqueue path with only the broker stubbed out, so the assertions
    are against the task package that would actually have been handed to Redis, and the
    Schedule row that would actually have been written.
    """

    def _run_bulk_action(self, sending_date):
        captured = {}

        def fake_enqueue(pack):
            captured.update(SignedPackage.loads(pack))
            return "fake-task-id"

        broker = MagicMock()
        broker.enqueue.side_effect = fake_enqueue
        broker.cache = None

        campaign = MagicMock()
        campaign.state = 0
        campaign.people_filter_id = "filter-1"
        campaign.utm_id = "abc123"
        campaign.subject = "Test campaign"
        campaign.sending_date = sending_date

        with patch("django_q.tasks.get_broker", return_value=broker):
            ScheduleEmailCampaignAction.execute_action([campaign])
        return captured

    def test_immediate_send_queues_send_emails_for_the_campaign(self):
        captured = self._run_bulk_action(sending_date=None)

        self.assertEqual(captured["func"], "underground_email.tasks.send_emails")
        self.assertEqual(captured["args"], ("abc123",))

    def test_scheduled_send_stores_send_emails_for_when_it_fires(self):
        """
        The scheduled path enqueues nothing now — it writes a Schedule row whose stored
        arguments are handed to async_task when it fires. The arguments are read back
        with ast.literal_eval because that is precisely what django_q/scheduler.py does.
        """
        captured = self._run_bulk_action(sending_date=timezone.now())

        self.assertEqual(captured, {}, "a scheduled send should not enqueue immediately")
        row = Schedule.objects.get(func="underground_email.tasks.send_emails")
        self.assertEqual(ast.literal_eval(row.args), ("abc123",))
