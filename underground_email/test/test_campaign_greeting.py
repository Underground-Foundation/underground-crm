"""
Tests for a campaign's optional greeting and preview text.

Both are optional, and both used to be printed as-is when missing, so a campaign without
them said "None": at the top of the email (the greeting) and in the recipient's inbox
summary after the subject line (the preview text). These tests check that a missing one
is left out altogether, both in the admin's preview and in the email that is sent.
"""

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from wagtail.models import Page, Site

from underground_crm.models import PeopleFilter
from underground_email.models import EmailCampaign, EmailSender, TemplatedGreeting
from underground_email.tasks import _render_email_html

GREETING_TEMPLATE = "Hi {{ preferred_or_first_name }},"
RECIPIENT_FIRST_NAME = "Robin"
RENDERED_GREETING = f"Hi {RECIPIENT_FIRST_NAME},"
PREVIEW_TEXT = "Can you spare an hour on Saturday?"
CAMPAIGN_TEMPLATE = "underground_email/smtp2go_base.html"
MISSING_VALUE_AS_TEXT = str(None)
MISSING_VALUE_EXPLANATION = (
    "Nothing in the base template says 'None' by itself, so it can only be a missing "
    "greeting or preview text printed as text."
)


class CampaignOptionalTextTests(TestCase):
    def setUp(self) -> None:
        self.recipient = get_user_model().objects.create_user(
            email="robin.nguyen@example.org",
            first_name=RECIPIENT_FIRST_NAME,
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
            site=Site.objects.create(
                hostname="vic.fusionparty.org.au",
                port=443,
                root_page=Page.get_first_root_node(),
            ),
            template=CAMPAIGN_TEMPLATE,
            people_filter=everyone,
            greeting=None,
            preview_text=None,
        )
        self.greeting = TemplatedGreeting.objects.create(greeting=GREETING_TEMPLATE)

    def _render_preview(self) -> str:
        request = RequestFactory().get("/cms/")
        request.user = self.recipient
        return render_to_string(
            self.campaign.get_preview_template(request, mode_name=""),
            self.campaign.get_preview_context(request, mode_name=""),
        )

    def _render_sent_email(self) -> str:
        return _render_email_html(self.campaign, self.recipient)

    def test_preview_without_greeting_or_preview_text_does_not_say_none(self) -> None:
        self.assertNotIn(MISSING_VALUE_AS_TEXT, self._render_preview(), MISSING_VALUE_EXPLANATION)

    def test_sent_email_without_greeting_or_preview_text_does_not_say_none(self) -> None:
        self.assertNotIn(
            MISSING_VALUE_AS_TEXT, self._render_sent_email(), MISSING_VALUE_EXPLANATION
        )

    def test_preview_shows_the_greeting_template_and_preview_text(self) -> None:
        self.campaign.greeting = self.greeting
        self.campaign.preview_text = PREVIEW_TEXT

        preview = self._render_preview()

        self.assertIn(
            GREETING_TEMPLATE,
            preview,
            "A preview has no recipient, so it shows the greeting's placeholders unrendered.",
        )
        self.assertIn(PREVIEW_TEXT, preview)

    def test_sent_email_without_a_greeting_has_no_greeting_paragraph(self) -> None:
        without_greeting = self._render_sent_email()
        self.campaign.greeting = self.greeting
        with_greeting = self._render_sent_email()

        self.assertIn(RENDERED_GREETING, with_greeting)
        self.assertEqual(
            without_greeting.count("<p>"),
            with_greeting.count("<p>") - 1,
            "Removing the greeting should remove its paragraph, rather than leave it empty.",
        )
