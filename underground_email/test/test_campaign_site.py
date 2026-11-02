"""
Tests for the site that an email campaign's links lead to.

Each campaign names a Wagtail Site, because one installation can serve several and the
recipient of an email should land on the one that wrote to them. The admin's own address
(WAGTAILADMIN_BASE_URL) used to be used instead, so these tests set it to something else
and check that it never reaches a recipient.
"""

import os
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from wagtail.models import Page, Site

from underground_crm.models import PeopleFilter
from underground_email.models import EmailCampaign, EmailSender
from underground_email.tasks import _render_email_html, send_emails

STATE_BRANCH_ROOT_URL = "https://vic.fusionparty.org.au"
ADMIN_ROOT_URL = "https://admin.fusionparty.org.au"


class CampaignSiteTests(TestCase):
    def setUp(self) -> None:
        self.state_branch_site = Site.objects.create(
            hostname="vic.fusionparty.org.au",
            port=443,
            root_page=Page.get_first_root_node(),
        )
        self.recipient = get_user_model().objects.create_user(
            email="robin.nguyen@example.org",
            first_name="Robin",
            last_name="Nguyen",
            email_opt_in=True,
        )
        secretary = get_user_model().objects.create_user(
            email="secretary@fusionparty.org.au", first_name="Sam", last_name="Okafor"
        )
        everyone = PeopleFilter.objects.create(
            name="Everyone in Victoria", criteria={"logic": "AND", "rules": []}
        )
        self.campaign = EmailCampaign.objects.create(
            subject="Volunteers needed in Brunswick this weekend",
            sender=EmailSender.objects.create(sender=secretary),
            site=self.state_branch_site,
            template="underground_email/smtp2go_base.html",
            people_filter=everyone,
        )

    def test_a_new_campaign_starts_on_the_default_site(self) -> None:
        default_site = Site.objects.get(is_default_site=True)

        self.assertEqual(EmailCampaign().site_id, default_site.pk)

    @override_settings(WAGTAILADMIN_BASE_URL=ADMIN_ROOT_URL)
    def test_rendered_links_lead_to_the_campaign_site(self) -> None:
        self.assertEqual(self.state_branch_site.root_url, STATE_BRANCH_ROOT_URL)

        html = _render_email_html(self.campaign, self.recipient)

        self.assertIn(f'href="{STATE_BRANCH_ROOT_URL}/unsubscribe?', html)
        self.assertIn(f'href="{STATE_BRANCH_ROOT_URL}/supporter_portal/memberships"', html)
        self.assertNotIn(ADMIN_ROOT_URL, html)

    @override_settings(WAGTAILADMIN_BASE_URL=ADMIN_ROOT_URL)
    @patch.dict(os.environ, {"SMTP_KEY": "api-1234567890"})
    def test_queued_send_needs_only_the_campaign_to_find_its_site(self) -> None:
        """
        Django Q stores the arguments of a scheduled send and replays them when it fires,
        so send_emails must work from the campaign's identifier alone. SMTP2Go is the only
        thing stubbed, so that no real email leaves and the outgoing HTML can be inspected.
        """
        with patch("underground_email.tasks.Smtp2goClient") as client_class:
            client: MagicMock = client_class.return_value
            client.send.return_value.success = True
            send_emails(self.campaign.utm_id)

        # The secretary never opted into email, which leaves Robin as the only recipient.
        client.send.assert_called_once()
        sent_email = client.send.call_args.kwargs
        self.assertEqual(
            sent_email["recipients"], [f"{self.recipient.full_name} <{self.recipient.email}>"]
        )
        self.assertIn(f'href="{STATE_BRANCH_ROOT_URL}/unsubscribe?', sent_email["html"])
        self.assertNotIn(ADMIN_ROOT_URL, sent_email["html"])
