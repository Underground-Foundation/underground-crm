"""
Tests for the CSRF failure view, which exists so that a refused donation is explained
in the server's logs instead of surfacing only as the form's "Network error".
"""

import json

from django.test import Client, TestCase
from django.urls import reverse
from wagtail.models import Page

from underground_crm.csrf import count_cookies_named
from underground_payments.models import PaymentPage

COOKIE_NAME = "csrftoken"
HOST_ONLY_TOKEN = "Rg7zKP4WKgLmbzeFzmYQmnwVkS4JOLcQ"
PARENT_DOMAIN_TOKEN = "q3VnE8cXw1ZsLpAoJbT6uYdMfH2kRiGa"


class CountCookiesNamedTests(TestCase):
    def test_counts_a_cookie_sent_twice_under_one_name(self):
        header = (
            f"sessionid=k3j2h1g4f5d6s7a8; {COOKIE_NAME}={PARENT_DOMAIN_TOKEN}; "
            f"{COOKIE_NAME}={HOST_ONLY_TOKEN}"
        )

        self.assertEqual(
            count_cookies_named(header, COOKIE_NAME),
            2,
            "Both the parent-domain and the host-only cookie were named in the header.",
        )

    def test_does_not_count_a_cookie_whose_name_merely_contains_the_name(self):
        header = f"old_{COOKIE_NAME}={PARENT_DOMAIN_TOKEN}"

        self.assertEqual(
            count_cookies_named(header, COOKIE_NAME),
            0,
            "old_csrftoken is a different cookie from csrftoken.",
        )

    def test_counts_nothing_when_no_cookies_were_sent(self):
        self.assertEqual(count_cookies_named("", COOKIE_NAME), 0)


class LoggedCsrfFailureTests(TestCase):
    def setUp(self):
        self.page = PaymentPage(title="Donate to Fusion", slug="donate")
        Page.objects.get(id=1).add_child(instance=self.page)

    def test_a_refused_donation_is_logged_with_its_reason(self):
        visitor = Client(enforce_csrf_checks=True)

        with self.assertLogs("underground_crm.csrf", level="WARNING") as captured:
            response = visitor.post(
                reverse("payment_create_intent", args=[self.page.pk]),
                data=json.dumps({"amount_cents": 2500, "frequency": "once"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 403, "The visitor sent no CSRF cookie or token.")
        message = captured.output[0]
        self.assertIn("CSRF cookie not set", message, "The reason Django gave should be logged.")
        self.assertIn(
            "csrftoken cookies sent=0", message, "The visitor's browser sent no CSRF cookie."
        )
