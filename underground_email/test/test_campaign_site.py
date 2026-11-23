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
from underground_email.models import EmailCampaign, EmailSender, SubscriptionPage
from underground_email.tasks import _render_email_html, send_email_batch

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

    def unsubscription_page_url(self) -> str:
        """Where the campaign site serves the subscription page that migration
        0018 created beneath the default site's root page. This site's root is
        the top of the page tree, so the page's path on it starts with the
        default site's root page."""
        page = SubscriptionPage.objects.get()
        default_root = Site.objects.get(is_default_site=True).root_page
        return f"{STATE_BRANCH_ROOT_URL}/{default_root.slug}/{page.slug}/"

    def test_a_new_campaign_starts_on_the_default_site(self) -> None:
        default_site = Site.objects.get(is_default_site=True)

        self.assertEqual(EmailCampaign().site_id, default_site.pk)

    @override_settings(WAGTAILADMIN_BASE_URL=ADMIN_ROOT_URL)
    def test_rendered_links_lead_to_the_campaign_site(self) -> None:
        self.assertEqual(self.state_branch_site.root_url, STATE_BRANCH_ROOT_URL)

        html = _render_email_html(self.campaign, self.recipient)

        self.assertIn(f'href="{self.unsubscription_page_url()}?', html)
        self.assertIn(f'href="{STATE_BRANCH_ROOT_URL}/membership"', html)
        self.assertNotIn(ADMIN_ROOT_URL, html)

    @override_settings(WAGTAILADMIN_BASE_URL=ADMIN_ROOT_URL)
    @patch.dict(os.environ, {"SMTP_KEY": "api-1234567890"})
    def test_queued_batch_needs_only_the_campaign_to_find_its_site(self) -> None:
        """
        Django Q stores the arguments of a queued batch and replays them when it runs,
        so send_email_batch must work from the campaign's identifier and its range of IDs
        alone. SMTP2Go is the only thing stubbed, so that no real email leaves and the
        outgoing HTML can be inspected.
        """
        with patch("underground_email.tasks.requests.Session") as session_class:
            session = _accepting_session(session_class)
            send_email_batch(self.campaign.utm_id, None, None)

        # The secretary never opted into email, which leaves Robin as the only recipient.
        session.post.assert_called_once()
        sent_email = session.post.call_args.kwargs["json"]
        self.assertEqual(sent_email["to"], [f"{self.recipient.full_name} <{self.recipient.email}>"])
        self.assertIn(f'href="{self.unsubscription_page_url()}?', sent_email["html_body"])
        self.assertNotIn(ADMIN_ROOT_URL, sent_email["html_body"])
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.sent_count, 1)

    @patch.dict(os.environ, {"SMTP_KEY": "api-1234567890"})
    def test_every_recipient_in_a_batch_is_sent_through_one_session(self) -> None:
        """
        Each recipient is a separate request, so a batch that opened a connection per
        request would spend much of its time establishing connections to SMTP2Go
        rather than sending through them.
        """
        get_user_model().objects.create_user(
            email="alex.morgan@example.org",
            first_name="Alex",
            last_name="Morgan",
            email_opt_in=True,
        )

        with patch("underground_email.tasks.requests.Session") as session_class:
            session = _accepting_session(session_class)
            send_email_batch(self.campaign.utm_id, None, None)

        session_class.assert_called_once()
        self.assertEqual(session.post.call_count, 2)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.sent_count, 2)


def _accepting_session(session_class: MagicMock) -> MagicMock:
    """Stub the requests.Session that send_email_batch opens, so that SMTP2Go accepts every
    email (https://developers.smtp2go.com/reference/send-standard-email)."""
    session: MagicMock = session_class.return_value.__enter__.return_value
    session.headers = {}
    response = session.post.return_value
    response.ok = True
    response.json.return_value = {
        "request_id": "aa253464-0bd0-467a-b24b-6159dcd7be60",
        "data": {"succeeded": 1, "failed": 0, "failures": []},
    }
    return session
