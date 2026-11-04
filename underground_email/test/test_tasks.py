import json
import os
import unittest
from typing import Optional
from unittest.mock import MagicMock, patch

import django.test
from django.contrib.auth import get_user_model

from underground_email.api import SMTP2GoEventType
from underground_email.models import EmailCampaign, generate_base64_id
from underground_email.tasks import process_email_engagements, process_email_rejection

Person = get_user_model()


class EmailRejectionTest(django.test.TestCase):
    RECIPIENT_EMAIL = "sam.fullmailbox@example.com"

    def _email_is_bad_after(self, event_type: SMTP2GoEventType) -> Optional[bool]:
        person = Person.objects.create_user(
            email=self.RECIPIENT_EMAIL, password="password", email_opt_in=True
        )
        campaign = EmailCampaign(
            utm_id=generate_base64_id(),
            subject="Volunteers needed in Brunswick this weekend",
            preview_text="Can you spare an hour on Saturday?",
        )

        process_email_rejection(
            campaign,
            recipients={person.email: person},
            events=[{"recipient": person.email, "event": event_type}],
        )

        person.refresh_from_db()
        return person.email_is_bad

    def test_soft_bounce_does_not_mark_the_address_as_bad(self):
        self.assertIsNone(
            self._email_is_bad_after(SMTP2GoEventType.SOFT_BOUNCED),
            "A soft bounce is temporary, so the address must stay unassessed.",
        )

    def test_hard_bounce_marks_the_address_as_bad(self):
        self.assertTrue(
            self._email_is_bad_after(SMTP2GoEventType.HARD_BOUNCED),
            "A hard bounce means that the address cannot receive emails.",
        )

    def test_rejection_marks_the_address_as_bad(self):
        self.assertTrue(
            self._email_is_bad_after(SMTP2GoEventType.REJECTED),
            "Rejected events have always marked the address as bad, and still should.",
        )


class TaskTest(unittest.TestCase):

    def _load_sample_events(self):
        sample_path = os.path.join(os.path.dirname(__file__), "click_sample.json")
        with open(sample_path) as f:
            return json.load(f)["data"]["events"]

    @patch("underground_crm.models.Engagement")
    def test_click_events_produces_engagements_without_persisting(self, mock_engagement_cls):
        mock_engagement_cls.EMAIL_OPENED = "email_opened"
        mock_engagement_cls.EMAIL_CLICKED = "email_clicked"
        mock_engagement_cls.objects.filter.return_value.values_list.return_value = []

        campaign = EmailCampaign(
            utm_id=generate_base64_id(),
            subject="Test email campaign",
            preview_text="Open this email",
        )
        mock_person = MagicMock()
        mock_person.pk = "test-pk"
        recipients = {"owen.miller@fusionparty.org.au": mock_person}

        recognized, new_opens, new_clicks = process_email_engagements(
            campaign, recipients, self._load_sample_events(), persist=False
        )

        self.assertEqual(recognized, 0)
        self.assertEqual(new_opens, 0)
        self.assertEqual(new_clicks, 1)
        mock_engagement_cls.objects.bulk_create.assert_not_called()
