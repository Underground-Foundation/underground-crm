"""
Tests for what happens when a recipient follows the unsubscription link in a campaign.

Each link carries a token signed by make_unsubscription_url, the same way a delivered
email does, and is followed through the real unsubscription_view.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from wagtail.models import Site

from underground_crm.models import Tag
from underground_email.models import EmailCampaign, EmailSender
from underground_email.unsubscription import make_unsubscription_url

INVALID_LINK_TEMPLATE = "underground_email/unsubscribe_invalid.html"
CONFIRMATION_TEMPLATE = "underground_email/unsubscribe_confirmed.html"
# A relative root keeps the link on Django's test client rather than on a real host.
TEST_CLIENT_ROOT_URL = ""


class UnsubscriptionLinkTests(TestCase):
    def setUp(self) -> None:
        person_model = get_user_model()
        self.newsletter_tag = Tag.objects.create(name="Monthly newsletter")
        self.recipient = person_model.objects.create_user(
            email="robin.nguyen@example.org",
            first_name="Robin",
            last_name="Nguyen",
            email_opt_in=True,
        )
        self.recipient.tags.add(self.newsletter_tag)
        secretary = person_model.objects.create_user(
            email="secretary@fusionparty.org.au", first_name="Sam", last_name="Okafor"
        )
        self.campaign = EmailCampaign.objects.create(
            subject="The Fusion monthly newsletter for November",
            sender=EmailSender.objects.create(sender=secretary),
            site=Site.objects.get(is_default_site=True),
            template="underground_email/smtp2go_base.html",
            unsubscription_tag=self.newsletter_tag,
        )

    def follow_link(self):
        url = make_unsubscription_url(self.recipient, self.campaign.utm_id, TEST_CLIENT_ROOT_URL)
        return self.client.get(url)

    def test_removes_only_the_campaigns_tag(self) -> None:
        response = self.follow_link()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, CONFIRMATION_TEMPLATE)
        self.assertFalse(
            self.recipient.tags.filter(pk=self.newsletter_tag.pk).exists(),
            "The campaign names a tag, so following its link should remove that tag.",
        )
        self.recipient.refresh_from_db()
        self.assertIsNone(
            self.recipient.unsubscribed_at,
            "Leaving one mailing list must not unsubscribe the person from all email.",
        )

    def test_a_recipient_who_has_since_lost_the_tag_is_still_told_it_worked(self) -> None:
        self.recipient.tags.remove(self.newsletter_tag)

        response = self.follow_link()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, CONFIRMATION_TEMPLATE)

    def test_a_link_for_a_deleted_campaign_is_reported_as_invalid(self) -> None:
        url = make_unsubscription_url(self.recipient, self.campaign.utm_id, TEST_CLIENT_ROOT_URL)
        self.campaign.delete()

        response = self.client.get(url)

        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, INVALID_LINK_TEMPLATE)

    def test_a_link_for_a_deleted_person_is_reported_as_invalid(self) -> None:
        url = make_unsubscription_url(self.recipient, self.campaign.utm_id, TEST_CLIENT_ROOT_URL)
        self.recipient.delete()

        response = self.client.get(url)

        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, INVALID_LINK_TEMPLATE)

    def test_a_tampered_link_is_reported_as_invalid(self) -> None:
        response = self.client.get(
            reverse("unsubscribe"),
            {"utm_campaign": self.campaign.utm_id, "address": "robin.nguyen@example.org"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, INVALID_LINK_TEMPLATE)
