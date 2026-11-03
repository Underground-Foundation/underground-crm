"""
Tests for the webhook through which SMTP2Go reports what recipients did with an
email. Each test posts a payload through the real URL, as SMTP2Go would.
"""

import json
from typing import Any

import django.test
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import override_settings
from django.urls import reverse

from underground_email.api import SMTP2GoEventType

Person = get_user_model()

RECIPIENT_EMAIL = "robin.bystander@fusionparty.org.au"
SUBJECT = "July newsletter"
WEBHOOK_SECRET = "3f2b8c1e-7a4d-4e96-b0d5-92c6a1f4e837"
AUTHORIZATION = f"Bearer {WEBHOOK_SECRET}"


@override_settings(SMTP2GO_WEBHOOK_SECRET=WEBHOOK_SECRET)
class EmailWebhookAuthorizationTest(django.test.TestCase):
    def setUp(self) -> None:
        self.recipient = Person.objects.create_user(
            email=RECIPIENT_EMAIL,
            password="correct-horse-battery",
            is_supporter=True,
            email_opt_in=True,
        )
        self.url = reverse("email_webhook")
        self.spam_report = json.dumps({"event": SMTP2GoEventType.SPAM, "rcpt": RECIPIENT_EMAIL})

    def _assert_recipient_untouched(self) -> None:
        self.recipient.refresh_from_db()
        self.assertTrue(
            self.recipient.is_supporter,
            "A forged spam report must not be able to remove somebody as a supporter.",
        )
        self.assertTrue(self.recipient.email_opt_in)
        self.assertIsNone(self.recipient.unsubscribed_at)

    def test_forged_reports_are_refused(self) -> None:
        forged_headers = {
            "no header": {},
            "the wrong secret": {"Authorization": "Bearer 11111111-2222-4333-8444-555555555555"},
            "the right secret under another scheme": {"Authorization": f"Basic {WEBHOOK_SECRET}"},
            "a scheme with no secret": {"Authorization": "Bearer"},
        }
        for description, headers in forged_headers.items():
            with self.subTest(description):
                response = self.client.post(
                    self.url,
                    data=self.spam_report,
                    content_type="application/json",
                    headers=headers,
                )

                self.assertEqual(response.status_code, 401)
                self._assert_recipient_untouched()

    def test_authorization_scheme_is_case_insensitive(self) -> None:
        response = self.client.post(
            self.url,
            data=self.spam_report,
            content_type="application/json",
            headers={"Authorization": f"bearer {WEBHOOK_SECRET}"},
        )

        self.assertEqual(
            response.status_code,
            200,
            "RFC 9110 makes the authentication scheme case-insensitive.",
        )

    @override_settings(SMTP2GO_WEBHOOK_SECRET="")
    def test_unconfigured_secret_accepts_nothing(self) -> None:
        self.client.raise_request_exception = False

        response = self.client.post(
            self.url,
            data=self.spam_report,
            content_type="application/json",
            # An empty secret would otherwise match this empty token.
            headers={"Authorization": "Bearer "},
        )

        self.assertEqual(
            response.status_code,
            500,
            "A missing secret is our misconfiguration, which should be raised loudly.",
        )
        self._assert_recipient_untouched()


@override_settings(SMTP2GO_WEBHOOK_SECRET=WEBHOOK_SECRET)
class EmailWebhookTest(django.test.TestCase):
    def setUp(self) -> None:
        self.recipient = Person.objects.create_user(
            email=RECIPIENT_EMAIL,
            password="correct-horse-battery",
            is_supporter=True,
            email_opt_in=True,
        )

    def _post_event(self, **payload: Any) -> HttpResponse:
        return self.client.post(
            reverse("email_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": AUTHORIZATION},
        )

    def test_spam_report_removes_the_supporter(self) -> None:
        response = self._post_event(
            event=SMTP2GoEventType.SPAM, rcpt=RECIPIENT_EMAIL, subject=SUBJECT
        )

        self.assertEqual(response.status_code, 200)
        self.recipient.refresh_from_db()
        self.assertFalse(self.recipient.is_supporter)
        self.assertFalse(self.recipient.email_opt_in)
        self.assertIsNotNone(self.recipient.unsubscribed_at)

    def test_unsubscription_leaves_them_a_supporter(self) -> None:
        response = self._post_event(
            event=SMTP2GoEventType.UNSUBSCRIBED, rcpt=RECIPIENT_EMAIL, subject=SUBJECT
        )

        self.assertEqual(response.status_code, 200)
        self.recipient.refresh_from_db()
        self.assertTrue(
            self.recipient.is_supporter,
            "Only a spam report removes somebody as a supporter through this webhook.",
        )
        self.assertFalse(self.recipient.email_opt_in)
        self.assertIsNotNone(self.recipient.unsubscribed_at)

    def test_report_without_a_subject_is_still_applied(self) -> None:
        for event_type in (SMTP2GoEventType.SPAM, SMTP2GoEventType.UNSUBSCRIBED):
            with self.subTest(event_type=event_type):
                Person.objects.filter(pk=self.recipient.pk).update(
                    email_opt_in=True, unsubscribed_at=None
                )
                response = self._post_event(event=event_type, rcpt=RECIPIENT_EMAIL)

                self.assertEqual(response.status_code, 200)
                self.recipient.refresh_from_db()
                self.assertFalse(
                    self.recipient.email_opt_in,
                    "The subject is only used for logging, so its absence must not "
                    "lose the recipient's decision.",
                )

    def test_address_with_an_uppercase_domain_finds_the_person(self) -> None:
        local_part, domain = RECIPIENT_EMAIL.split("@")

        self._post_event(event=SMTP2GoEventType.SPAM, rcpt=f"{local_part}@{domain.upper()}")

        self.recipient.refresh_from_db()
        self.assertFalse(
            self.recipient.is_supporter,
            "Stored addresses have lowercase domains, as normalize_email produces them.",
        )

    def test_unknown_address_is_acknowledged(self) -> None:
        response = self._post_event(
            event=SMTP2GoEventType.SPAM, rcpt="nobody.known@fusionparty.org.au", subject=SUBJECT
        )

        self.assertEqual(
            response.status_code,
            200,
            "SMTP2Go cannot do anything about an address we do not hold, so this is "
            "logged on our side rather than reported back to it as a failure.",
        )
