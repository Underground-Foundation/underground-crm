import json
import os
import unittest
from unittest.mock import MagicMock, patch

from underground_email.models import EmailCampaign, generate_base64_id
from underground_email.tasks import process_email_engagements


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
