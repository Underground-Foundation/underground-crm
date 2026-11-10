"""
Tests for when a mailing-list subscriber is opted into email and their address is known
to work.

Somebody who subscribes anonymously with an address we have never seen could have typed
anybody's address, so the "Emailable addresses" filter (which requires email_opt_in) must
not reach them until they follow the link in the "confirm your subscription" email.
Following it also proves that the address receives email, which Person records as
email_is_bad=False. The allauth EmailAddress row behind the link is deleted afterwards,
so that Person is the only record of it.

Two things are stubbed, both because they are external services: the Django Q queue
(Redis), whose queued call is captured and then run in-process, and SMTP2Go, so that no
real email leaves and the confirmation link can be read from the outgoing HTML.
"""

import os
import re
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from underground_crm.models.person import Tag
from underground_crm.tasks import send_subscription_confirmation

Person = get_user_model()

NEWSLETTER_TAG_NAME = "Newsletter"
SUBSCRIBER_EMAIL = "robin.nguyen@example.org"
CONFIRMATION_LINK_PATTERN = re.compile(r'href="([^"]*/account/confirm-email/[^"]+)"')


@override_settings(SELF_QUERYABLE_TAGS=[NEWSLETTER_TAG_NAME])
@patch.dict(os.environ, {"SMTP_KEY": "api-1234567890"})
class SubscriptionOptInTests(TestCase):
    def setUp(self) -> None:
        Tag.objects.get_or_create(name=NEWSLETTER_TAG_NAME)
        self.home_page = Site.objects.get(is_default_site=True).root_page

    def _subscribe(self) -> MagicMock:
        """Subscribe through the public endpoint, returning the stubbed task queue."""
        with patch("underground_crm.signals.async_task") as async_task:
            response = self.client.post(
                reverse("underground_crm:subscribe"),
                data={
                    "tag_name": NEWSLETTER_TAG_NAME,
                    "page_id": self.home_page.pk,
                    "email": SUBSCRIBER_EMAIL,
                    "first_name": "Robin",
                    "last_name": "Nguyen",
                },
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        return async_task

    def _send_confirmation_email(self, async_task: MagicMock) -> str:
        """Run the queued confirmation task and return the link from its email."""
        async_task.assert_called_once()
        task_name, *task_args = async_task.call_args.args
        self.assertEqual(task_name, "underground_crm.tasks.send_subscription_confirmation")
        with patch("smtp2go.core.Smtp2goClient") as client_class:
            client: MagicMock = client_class.return_value
            client.send.return_value.success = True
            send_subscription_confirmation(*task_args)
        match = CONFIRMATION_LINK_PATTERN.search(client.send.call_args.kwargs["html"])
        self.assertIsNotNone(match, "The confirmation email should contain a confirmation link.")
        return match.group(1)

    def test_new_subscriber_is_opted_in_only_after_confirming(self) -> None:
        confirmation_link = self._send_confirmation_email(self._subscribe())

        subscriber = Person.objects.get(email=SUBSCRIBER_EMAIL)
        self.assertFalse(
            subscriber.email_opt_in,
            "Anybody could have typed this address, so it must not be emailable yet.",
        )
        self.assertIsNone(
            subscriber.email_is_bad, "Nothing is known yet about whether the address works."
        )

        # allauth confirms on POST; a GET only shows the page with the button to press.
        response = self.client.post(urlparse(confirmation_link).path)
        self.assertEqual(response.status_code, 302, "allauth redirects after confirming.")

        subscriber.refresh_from_db()
        self.assertTrue(subscriber.email_opt_in)
        self.assertIs(subscriber.email_is_bad, False)
        self.assertFalse(EmailAddress.objects.filter(user=subscriber).exists())

    def test_subscriber_whose_address_is_known_to_work_is_not_emailed(self) -> None:
        async_task = self._subscribe()
        Person.objects.filter(email=SUBSCRIBER_EMAIL).update(email_is_bad=False)
        task_name, *task_args = async_task.call_args.args

        with patch("smtp2go.core.Smtp2goClient") as client_class:
            send_subscription_confirmation(*task_args)

        client_class.return_value.send.assert_not_called()

    def test_existing_subscriber_is_opted_in_straight_away(self) -> None:
        """
        No confirmation email goes to an address that is already on record, so the
        subscription opts it in directly, as it always has.
        """
        existing = Person.objects.create_user(
            email=SUBSCRIBER_EMAIL, first_name="Robin", last_name="Nguyen"
        )

        async_task = self._subscribe()

        async_task.assert_not_called()
        existing.refresh_from_db()
        self.assertTrue(existing.email_opt_in)

    def test_verifying_a_website_account_does_not_opt_in(self) -> None:
        """
        allauth sends the same email_confirmed signal when somebody verifies the address
        of a website account. That proves the address works, but it is not consent to
        receive email.
        """
        account_holder = Person.objects.create_user(
            email=SUBSCRIBER_EMAIL, first_name="Robin", last_name="Nguyen"
        )
        email_address = EmailAddress.objects.create(
            user=account_holder, email=account_holder.email, primary=True, verified=False
        )

        response = self.client.post(
            reverse("account_confirm_email", args=[EmailConfirmationHMAC(email_address).key])
        )
        self.assertEqual(response.status_code, 302, "allauth redirects after confirming.")

        account_holder.refresh_from_db()
        self.assertIs(account_holder.email_is_bad, False)
        self.assertFalse(account_holder.email_opt_in)
        self.assertFalse(EmailAddress.objects.filter(user=account_holder).exists())
