"""
Tests for the queued-task timeout applied to campaign sends.

Q_CLUSTER sets a two-minute ceiling so a wedged task cannot occupy a worker forever, but
send_emails issues one SMTP2Go request per recipient in a single task and legitimately
runs far longer than that on a list of any size.  It therefore overrides the ceiling at
both of its enqueue sites.  Losing that override would not fail loudly: campaigns would
simply stop part-way, after some recipients had already been emailed and before the
campaign was marked sent.
"""

import ast
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from django_q.conf import Conf
from django_q.models import Schedule
from django_q.signing import SignedPackage

from underground_email.tasks import CAMPAIGN_SEND_TIMEOUT_SECONDS
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

    def test_campaign_sends_are_given_room_beyond_the_cluster_ceiling(self):
        self.assertGreater(CAMPAIGN_SEND_TIMEOUT_SECONDS, Conf.TIMEOUT)


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

    def test_immediate_send_carries_the_longer_timeout(self):
        captured = self._run_bulk_action(sending_date=None)

        self.assertEqual(captured["func"], "underground_email.tasks.send_emails")
        self.assertEqual(captured["timeout"], CAMPAIGN_SEND_TIMEOUT_SECONDS)

    def test_timeout_is_a_task_option_not_an_argument_to_send_emails(self):
        """
        django-q2 pops "timeout" out of the keyword arguments and treats it as a task
        option.  If that ever stopped being true it would reach send_emails() as an
        unexpected keyword argument and every campaign send would fail immediately.
        """
        captured = self._run_bulk_action(sending_date=None)

        self.assertEqual(captured["args"], ("abc123",))
        self.assertNotIn("timeout", captured.get("kwargs", {}))

    def test_scheduled_send_stores_the_longer_timeout_for_when_it_fires(self):
        """
        The scheduled path enqueues nothing now — it writes a Schedule row whose stored
        kwargs are handed to async_task when it fires, where "timeout" is picked up as a
        task option in exactly the same way.  The kwargs are read back with
        ast.literal_eval because that is precisely what django_q/scheduler.py does.
        """
        captured = self._run_bulk_action(sending_date=timezone.now())

        self.assertEqual(captured, {}, "a scheduled send should not enqueue immediately")
        row = Schedule.objects.get(func="underground_email.tasks.send_emails")
        self.assertEqual(ast.literal_eval(row.kwargs)["timeout"], CAMPAIGN_SEND_TIMEOUT_SECONDS)
