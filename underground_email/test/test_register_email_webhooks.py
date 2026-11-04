"""
Tests for choosing which site the register_email_webhooks command registers.

One installation can serve several sites, and SMTP2Go delivers its notifications to whichever
address we give it. SMTP2Go itself is the only thing mocked here: a real call would add a
webhook to a live account, so we capture the request that would have been sent to it instead.
"""

import os
from typing import Optional
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Page, Site

COMMAND_NAME = "register_email_webhooks"
HTTPS_PORT = 443
ALTERNATE_HTTPS_PORT = 8443

NATIONAL_DOMAIN = "fusionparty.org.au"
STATE_BRANCH_DOMAIN = "vic.fusionparty.org.au"
UNKNOWN_DOMAIN = "nsw.fusionparty.org.au"


@override_settings(SMTP2GO_WEBHOOK_SECRET="webhook-secret-0987654321")
@patch.dict(os.environ, {"SMTP_KEY": "api-1234567890"})
class RegisterEmailWebhooksCommandTests(TestCase):
    def setUp(self) -> None:
        # The default site is created by Wagtail's migrations with a local hostname, which the
        # task refuses to register, so it is given a public one here.
        self.default_site = Site.objects.get(is_default_site=True)
        self.default_site.hostname = NATIONAL_DOMAIN
        self.default_site.port = HTTPS_PORT
        self.default_site.save()
        self.state_branch_site = Site.objects.create(
            hostname=STATE_BRANCH_DOMAIN,
            port=HTTPS_PORT,
            root_page=Page.get_first_root_node(),
        )

    def _registered_webhook_url(self, domain: Optional[str] = None) -> str:
        """Runs the command and returns the URL that it would have given to SMTP2Go."""
        arguments = [] if domain is None else ["--domain", domain]
        with patch("underground_email.tasks.requests.post") as post_to_smtp2go:
            call_command(COMMAND_NAME, *arguments)
        post_to_smtp2go.assert_called_once()
        return post_to_smtp2go.call_args.kwargs["json"]["url"]

    def test_the_default_site_is_used_when_no_domain_is_given(self) -> None:
        self.assertEqual(
            self._registered_webhook_url(),
            self.default_site.root_url + reverse("email_webhook"),
        )

    def test_the_site_with_the_given_domain_is_used(self) -> None:
        self.assertEqual(
            self._registered_webhook_url(domain=STATE_BRANCH_DOMAIN),
            self.state_branch_site.root_url + reverse("email_webhook"),
        )

    def test_the_capitalization_of_the_domain_does_not_matter(self) -> None:
        self.assertEqual(
            self._registered_webhook_url(domain=STATE_BRANCH_DOMAIN.upper()),
            self.state_branch_site.root_url + reverse("email_webhook"),
        )

    def test_an_unknown_domain_is_refused_and_the_known_ones_are_listed(self) -> None:
        with patch("underground_email.tasks.requests.post") as post_to_smtp2go:
            with self.assertRaises(CommandError) as raised:
                call_command(COMMAND_NAME, "--domain", UNKNOWN_DOMAIN)

        post_to_smtp2go.assert_not_called()
        self.assertIn(UNKNOWN_DOMAIN, str(raised.exception))
        self.assertIn(NATIONAL_DOMAIN, str(raised.exception))
        self.assertIn(STATE_BRANCH_DOMAIN, str(raised.exception))

    def test_a_domain_served_on_two_ports_is_refused_rather_than_guessed(self) -> None:
        second_port_site = Site.objects.create(
            hostname=STATE_BRANCH_DOMAIN,
            port=ALTERNATE_HTTPS_PORT,
            root_page=Page.get_first_root_node(),
        )

        with patch("underground_email.tasks.requests.post") as post_to_smtp2go:
            with self.assertRaises(CommandError) as raised:
                call_command(COMMAND_NAME, "--domain", STATE_BRANCH_DOMAIN)

        post_to_smtp2go.assert_not_called()
        self.assertIn(self.state_branch_site.root_url, str(raised.exception))
        self.assertIn(second_port_site.root_url, str(raised.exception))

    def test_a_missing_default_site_is_refused_when_no_domain_is_given(self) -> None:
        self.default_site.is_default_site = False
        self.default_site.save()

        with patch("underground_email.tasks.requests.post") as post_to_smtp2go:
            with self.assertRaises(CommandError):
                call_command(COMMAND_NAME)

        post_to_smtp2go.assert_not_called()
