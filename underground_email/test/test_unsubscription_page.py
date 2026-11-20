"""
Tests for the UnsubscriptionPage served at /unsubscribe.

The page is about whoever the signed token in an email's unsubscription link
names, or else whoever is logged in. It lists their current memberships and
those of their tags named in SELF_QUERYABLE_TAGS, each with a button to cancel
or remove it, and says so when there are none of either.

Expected texts are written out as constants rather than copied from the
template, so that a test fails if the wording a visitor reads changes; the
suite runs in the site's default language (English), so they are not
translated.
"""

import datetime

import django.test
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from wagtail.models import Site

from underground_crm.models import Engagement
from underground_crm.models.form_submission import FormSubmission
from underground_crm.models.membership import Membership, MembershipType
from underground_crm.models.person import Tag
from underground_email.models import EmailCampaign, EmailSender, UnsubscriptionPage
from underground_email.unsubscription import generate_unsubscription_signature
from underground_crm.models import PeopleFilter

Person = get_user_model()

NEWSLETTER_TAG_NAME = "Newsletter"
PRESS_RELEASES_TAG_NAME = "Press releases"
# A tag that staff apply for their own purposes, which a person must neither
# see nor be able to remove on this page.
STAFF_ONLY_TAG_NAME = "Spoke to at Brunswick stall"
SELF_QUERYABLE_TAG_NAMES = [NEWSLETTER_TAG_NAME, PRESS_RELEASES_TAG_NAME]
# Not "Fusion Party", which the Fusion theme's branding would put on every page
# anyway, making a match incidental.
MEMBERSHIP_TYPE_NAME = "Pirate Party"
FUTURE_EXPIRY = timezone.localdate() + datetime.timedelta(days=365)
PAST_EXPIRY = timezone.localdate() - datetime.timedelta(days=30)

NOT_A_MEMBER_TEXT = "You are not a member."
NOT_SUBSCRIBED_TEXT = "You are not subscribed to the newsletter."
CANCEL_MEMBERSHIP_BUTTON_TEXT = "Cancel membership"

UNSUBSCRIPTION_PAGE_TITLE = "Manage your subscriptions"
UNSUBSCRIPTION_PAGE_SLUG = "manage-your-subscriptions"


@override_settings(SELF_QUERYABLE_TAGS=SELF_QUERYABLE_TAG_NAMES)
class UnsubscriptionPageTestCase(django.test.TestCase):
    def setUp(self) -> None:
        self.page = UnsubscriptionPage(
            title=UNSUBSCRIPTION_PAGE_TITLE, slug=UNSUBSCRIPTION_PAGE_SLUG
        )
        Site.objects.get(is_default_site=True).root_page.add_child(instance=self.page)
        self.url = reverse("unsubscribe")

        self.newsletter = Tag.objects.create(name=NEWSLETTER_TAG_NAME)
        self.press_releases = Tag.objects.create(name=PRESS_RELEASES_TAG_NAME)
        self.staff_only_tag = Tag.objects.create(name=STAFF_ONLY_TAG_NAME)
        self.membership_type = MembershipType.objects.create(name=MEMBERSHIP_TYPE_NAME)

        self.member = Person.objects.create_user(
            email="priya.sharma@example.com", first_name="Priya", last_name="Sharma"
        )

    def give_membership(self, expires_on: datetime.date = FUTURE_EXPIRY) -> Membership:
        return Membership.objects.create(
            person=self.member,
            type=self.membership_type,
            started_at=timezone.now(),
            expires_on=expires_on,
            is_approved=True,
        )

    def make_campaign(self, unsubscription_tag: Tag | None) -> EmailCampaign:
        secretary = Person.objects.create_user(
            email="secretary@fusionparty.org.au", first_name="Sam", last_name="Okafor"
        )
        return EmailCampaign.objects.create(
            subject="Volunteers needed in Brunswick this weekend",
            sender=EmailSender.objects.create(sender=secretary),
            template="underground_email/smtp2go_base.html",
            people_filter=PeopleFilter.objects.create(
                name="Everyone in Victoria", criteria={"logic": "AND", "rules": []}
            ),
            unsubscription_tag=unsubscription_tag,
        )

    def email_link(self, campaign: EmailCampaign) -> str:
        """The path of the unsubscription link in the member's copy of a campaign."""
        token = generate_unsubscription_signature(
            email_address=self.member.email, utm_id=campaign.utm_id
        )
        return f"{self.url}?utm_campaign={campaign.utm_id}&address={token}"


class ListingTests(UnsubscriptionPageTestCase):
    def test_a_member_sees_their_current_membership_and_self_queryable_tags(self) -> None:
        self.give_membership()
        self.member.tags.add(self.newsletter, self.staff_only_tag)
        self.client.force_login(self.member)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, MEMBERSHIP_TYPE_NAME)
        self.assertContains(response, CANCEL_MEMBERSHIP_BUTTON_TEXT)
        self.assertContains(response, NEWSLETTER_TAG_NAME)
        self.assertNotContains(
            response,
            STAFF_ONLY_TAG_NAME,
            msg_prefix="Only SELF_QUERYABLE_TAGS may be shown to a person; the rest are for staff.",
        )
        self.assertNotContains(response, NOT_A_MEMBER_TEXT)
        self.assertNotContains(response, NOT_SUBSCRIBED_TEXT)

    def test_info_boxes_are_shown_to_someone_with_neither_memberships_nor_subscriptions(
        self,
    ) -> None:
        self.give_membership(expires_on=PAST_EXPIRY)
        self.member.tags.add(self.staff_only_tag)
        self.client.force_login(self.member)

        response = self.client.get(self.url)

        self.assertContains(
            response,
            NOT_A_MEMBER_TEXT,
            msg_prefix="A membership that has expired does not make somebody a member.",
        )
        self.assertContains(
            response,
            NOT_SUBSCRIBED_TEXT,
            msg_prefix="A staff-only tag is not a subscription that the person can see.",
        )
        self.assertNotContains(response, CANCEL_MEMBERSHIP_BUTTON_TEXT)

    def test_the_two_info_boxes_are_independent(self) -> None:
        self.member.tags.add(self.newsletter)
        self.client.force_login(self.member)

        response = self.client.get(self.url)

        self.assertContains(response, NOT_A_MEMBER_TEXT)
        self.assertNotContains(response, NOT_SUBSCRIBED_TEXT)

    def test_an_anonymous_visitor_without_a_link_sees_nobody_s_details(self) -> None:
        self.member.tags.add(self.newsletter)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, NEWSLETTER_TAG_NAME)
        self.assertNotContains(
            response,
            NOT_A_MEMBER_TEXT,
            msg_prefix="An unidentified visitor's memberships are unknown, not absent.",
        )
        self.assertContains(response, reverse("underground_crm:login"))

    def test_the_page_s_place_in_the_tree_redirects_to_the_unsubscription_url(self) -> None:
        response = self.client.get(self.page.url + "?utm_campaign=AbC123")

        self.assertRedirects(
            response, self.url + "?utm_campaign=AbC123", fetch_redirect_response=False
        )

    def test_the_page_is_never_cached_publicly(self) -> None:
        self.client.force_login(self.member)

        response = self.client.get(self.url)

        self.assertIn("no-store", response.headers.get("Cache-Control", ""))


class ButtonTests(UnsubscriptionPageTestCase):
    def test_removing_a_tag_unsubscribes_the_person_and_records_it(self) -> None:
        self.member.tags.add(self.newsletter, self.press_releases)
        self.client.force_login(self.member)

        response = self.client.post(
            self.url,
            {"action": UnsubscriptionPage.REMOVE_TAG_ACTION, "tag_id": str(self.newsletter.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(self.member.tags.values_list("name", flat=True)), {PRESS_RELEASES_TAG_NAME}
        )
        self.assertTrue(
            Engagement.objects.filter(
                person=self.member,
                action_type=Engagement.UNSUBSCRIBED,
                metadata__tag=NEWSLETTER_TAG_NAME,
            ).exists()
        )
        self.assertFalse(
            FormSubmission.objects.exists(),
            "Pressing one of the page's buttons is not an answer to the page's form.",
        )

    def test_a_staff_only_tag_cannot_be_removed(self) -> None:
        self.member.tags.add(self.staff_only_tag)
        self.client.force_login(self.member)

        self.client.post(
            self.url,
            {
                "action": UnsubscriptionPage.REMOVE_TAG_ACTION,
                "tag_id": str(self.staff_only_tag.pk),
            },
        )

        self.assertTrue(self.member.tags.filter(pk=self.staff_only_tag.pk).exists())

    def test_a_malformed_tag_id_is_ignored(self) -> None:
        self.member.tags.add(self.newsletter)
        self.client.force_login(self.member)

        response = self.client.post(
            self.url, {"action": UnsubscriptionPage.REMOVE_TAG_ACTION, "tag_id": "newsletter"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.member.tags.filter(pk=self.newsletter.pk).exists())

    def test_cancelling_a_membership_ends_it_at_once(self) -> None:
        membership = self.give_membership()
        self.client.force_login(self.member)

        response = self.client.post(
            self.url,
            {
                "action": UnsubscriptionPage.CANCEL_MEMBERSHIP_ACTION,
                "membership_id": str(membership.pk),
            },
        )

        membership.refresh_from_db()
        self.assertFalse(membership.is_current)
        self.assertContains(
            response,
            NOT_A_MEMBER_TEXT,
            msg_prefix="The page shown after cancelling should reflect the cancellation.",
        )

    def test_nobody_can_cancel_somebody_else_s_membership(self) -> None:
        membership = self.give_membership()
        stranger = Person.objects.create_user(
            email="lucas.nguyen@example.com", first_name="Lucas", last_name="Nguyen"
        )
        self.client.force_login(stranger)

        self.client.post(
            self.url,
            {
                "action": UnsubscriptionPage.CANCEL_MEMBERSHIP_ACTION,
                "membership_id": str(membership.pk),
            },
        )

        membership.refresh_from_db()
        self.assertEqual(membership.expires_on, FUTURE_EXPIRY)

    def test_an_anonymous_visitor_without_a_link_cannot_press_buttons(self) -> None:
        membership = self.give_membership()

        self.client.post(
            self.url,
            {
                "action": UnsubscriptionPage.CANCEL_MEMBERSHIP_ACTION,
                "membership_id": str(membership.pk),
            },
        )

        membership.refresh_from_db()
        self.assertEqual(membership.expires_on, FUTURE_EXPIRY)


class EmailLinkTests(UnsubscriptionPageTestCase):
    def test_following_the_link_unsubscribes_and_shows_the_recipient_s_page(self) -> None:
        self.give_membership()
        self.member.tags.add(self.newsletter, self.press_releases)
        campaign = self.make_campaign(unsubscription_tag=self.newsletter)

        response = self.client.get(self.email_link(campaign))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.member.tags.filter(pk=self.newsletter.pk).exists())
        self.assertContains(
            response,
            MEMBERSHIP_TYPE_NAME,
            msg_prefix="The link identifies the recipient without their logging in.",
        )
        self.assertContains(response, PRESS_RELEASES_TAG_NAME)

    def test_buttons_pressed_after_following_the_link_identify_the_recipient_by_it(
        self,
    ) -> None:
        membership = self.give_membership()
        self.member.tags.add(self.press_releases)
        campaign = self.make_campaign(unsubscription_tag=self.newsletter)
        link = self.email_link(campaign)
        self.client.get(link)

        self.client.post(
            link,
            {
                "action": UnsubscriptionPage.CANCEL_MEMBERSHIP_ACTION,
                "membership_id": str(membership.pk),
            },
        )

        membership.refresh_from_db()
        self.assertFalse(membership.is_current)
        campaign.refresh_from_db()
        self.assertEqual(
            campaign.unsubscription_count,
            1,
            "Only following the link counts as unsubscribing from the campaign; pressing a "
            "button afterwards (a POST to the same address) must not count it again.",
        )

    def test_a_tampered_link_is_rejected(self) -> None:
        campaign = self.make_campaign(unsubscription_tag=self.newsletter)
        self.member.tags.add(self.newsletter)

        response = self.client.get(self.email_link(campaign) + "tampered")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.member.tags.filter(pk=self.newsletter.pk).exists())

    def test_without_a_page_the_link_still_unsubscribes(self) -> None:
        self.page.unpublish()
        self.member.tags.add(self.newsletter)
        campaign = self.make_campaign(unsubscription_tag=self.newsletter)

        response = self.client.get(self.email_link(campaign))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "underground_email/unsubscribe_confirmed.html")
        self.assertFalse(self.member.tags.filter(pk=self.newsletter.pk).exists())
