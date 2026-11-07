"""
Tests for the site that a "confirm your subscription" link leads to.

One installation can serve several Wagtail Sites, and somebody who subscribes on one of
them should confirm on that same site. The admin's own address (WAGTAILADMIN_BASE_URL)
used to be used instead, so these tests set it to something else and check that it never
reaches the subscriber.
"""

import os
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from wagtail.models import Page, Site

from underground_crm.models.person import Tag
from underground_crm.tasks import send_subscription_confirmation

STATE_BRANCH_ROOT_URL = "https://vic.fusionparty.org.au"
ADMIN_ROOT_URL = "https://admin.fusionparty.org.au"


class SubscriptionConfirmationSiteTests(TestCase):
    def setUp(self) -> None:
        self.state_branch_site = Site.objects.create(
            hostname="vic.fusionparty.org.au",
            port=443,
            root_page=Page.get_first_root_node(),
        )
        self.subscriber = get_user_model().objects.create_user(
            email="robin.nguyen@example.org", first_name="Robin", last_name="Nguyen"
        )
        self.newsletter = Tag.objects.create(name="Victorian branch newsletter")

    @override_settings(WAGTAILADMIN_BASE_URL=ADMIN_ROOT_URL)
    @patch.dict(os.environ, {"SMTP_KEY": "api-1234567890"})
    def test_confirmation_link_leads_to_the_site_subscribed_on(self) -> None:
        """
        SMTP2Go is the only thing stubbed, so that no real email leaves and the outgoing
        HTML can be inspected.
        """
        self.assertEqual(self.state_branch_site.root_url, STATE_BRANCH_ROOT_URL)

        with patch("smtp2go.core.Smtp2goClient") as client_class:
            client: MagicMock = client_class.return_value
            client.send.return_value.success = True
            send_subscription_confirmation(
                str(self.subscriber.pk), str(self.newsletter.pk), self.state_branch_site.pk
            )

        client.send.assert_called_once()
        sent_email = client.send.call_args.kwargs
        self.assertEqual(sent_email["recipients"], [self.subscriber.email])
        self.assertIn(f"{STATE_BRANCH_ROOT_URL}/account/", sent_email["html"])
        self.assertNotIn(ADMIN_ROOT_URL, sent_email["html"])
