"""
Tests for the UnsubscriptionPage served at /unsubscribe.

The page is about whoever the signed token in an email's unsubscription link
names, or else whoever is logged in. Its form lists their current memberships
and those of their tags named in SELF_QUERYABLE_TAGS, each with a ticked
checkbox; submitting it with a box unticked cancels that membership or removes
that tag. Following the link changes nothing by itself, because over-eager
email clients follow links that nobody clicked.

Every site is given the page by migration 0018, so these tests use the page
that the migrated test database already has, rather than creating one.

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
from wagtail.models import Page, Site

from underground_crm.models import Engagement, PeopleFilter
from underground_crm.models.form_submission import FormSubmission
from underground_crm.models.membership import Membership, MembershipType
from underground_crm.models.person import Tag
from underground_email.models import EmailCampaign, EmailSender, UnsubscriptionPage
from underground_email.unsubscription import generate_unsubscription_signature
from underground_email.unsubscription_form import TOKEN_FIELD_NAME

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
INVALID_LINK_TEXT = "This unsubscription link is not valid."
UNSUBSCRIBE_BUTTON_TEXT = "Unsubscribe"


@override_settings(SELF_QUERYABLE_TAGS=SELF_QUERYABLE_TAG_NAMES)
class UnsubscriptionPageTestCase(django.test.TestCase):
    def setUp(self) -> None:
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

    def make_campaign(self) -> EmailCampaign:
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
            unsubscription_tag=self.newsletter,
        )

    def token_for(self, campaign: EmailCampaign) -> str:
        """The signed token in the member's copy of a campaign's unsubscription link."""
        return generate_unsubscription_signature(
            email_address=self.member.email, utm_id=campaign.utm_id
        )

    def email_link(self, token: str) -> str:
        return f"{self.url}?{TOKEN_FIELD_NAME}={token}"


class MigrationTests(UnsubscriptionPageTestCase):
    def test_every_site_is_given_a_live_unsubscription_page(self) -> None:
        for site in Site.objects.all():
            self.assertTrue(
                UnsubscriptionPage.objects.live()
                .descendant_of(site.root_page, inclusive=True)
                .exists(),
                f"Migration 0018 should have given {site} an unsubscription page.",
            )

    def test_the_page_tree_is_intact(self) -> None:
        # The migration writes treebeard's materialized paths by hand, since
        # historical models lack the tree-editing methods.
        problems = Page.find_problems()
        self.assertFalse(any(problems), f"Treebeard found problems in the page tree: {problems}")

    def test_the_page_s_place_in_the_tree_redirects_to_the_unsubscription_url(self) -> None:
        page = UnsubscriptionPage.objects.get()
        query_string = f"{TOKEN_FIELD_NAME}=signed-token"

        response = self.client.get(f"{page.url}?{query_string}")

        self.assertRedirects(response, f"{self.url}?{query_string}", fetch_redirect_response=False)


class ListingTests(UnsubscriptionPageTestCase):
    def test_a_member_sees_their_membership_and_self_queryable_tags_ticked(self) -> None:
        membership = self.give_membership()
        self.member.tags.add(self.newsletter, self.staff_only_tag)
        self.client.force_login(self.member)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(list(form["memberships"].value()), [membership.pk])
        self.assertEqual(list(form["tags"].value()), [self.newsletter.pk])
        self.assertContains(response, MEMBERSHIP_TYPE_NAME)
        self.assertContains(response, NEWSLETTER_TAG_NAME)
        self.assertNotContains(
            response,
            STAFF_ONLY_TAG_NAME,
            msg_prefix="Only SELF_QUERYABLE_TAGS may be shown to a person; the rest are for staff.",
        )
        self.assertNotContains(response, NOT_A_MEMBER_TEXT)
        self.assertNotContains(response, NOT_SUBSCRIBED_TEXT)
        self.assertContains(response, UNSUBSCRIBE_BUTTON_TEXT)

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

    def test_the_page_is_never_cached_publicly(self) -> None:
        self.client.force_login(self.member)

        response = self.client.get(self.url)

        self.assertIn("no-store", response.headers.get("Cache-Control", ""))


class EmailLinkTests(UnsubscriptionPageTestCase):
    def test_following_the_link_changes_nothing(self) -> None:
        # An over-eager email client or security scanner follows the link
        # before the recipient has read the email.
        membership = self.give_membership()
        self.member.tags.add(self.newsletter)
        campaign = self.make_campaign()
        token = self.token_for(campaign)

        response = self.client.get(self.email_link(token))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.member.tags.filter(pk=self.newsletter.pk).exists())
        membership.refresh_from_db()
        self.assertTrue(membership.is_current)
        campaign.refresh_from_db()
        self.assertFalse(campaign.unsubscription_count)
        self.assertContains(
            response,
            f'<input type="hidden" name="{TOKEN_FIELD_NAME}" value="{token}"',
            msg_prefix="The link's token must be carried in a hidden field of the form.",
        )

    def test_the_link_identifies_the_recipient_without_their_logging_in(self) -> None:
        self.give_membership()
        campaign = self.make_campaign()

        response = self.client.get(self.email_link(self.token_for(campaign)))

        self.assertContains(response, MEMBERSHIP_TYPE_NAME)
        self.assertNotIn(
            "email",
            response.context["form"].fields,
            "The token has proved who the visitor is, so they need not type their address.",
        )

    def test_unticking_a_tag_and_submitting_unsubscribes_from_it_alone(self) -> None:
        self.member.tags.add(self.newsletter, self.press_releases, self.staff_only_tag)
        campaign = self.make_campaign()
        token = self.token_for(campaign)

        response = self.client.post(
            self.email_link(token),
            {TOKEN_FIELD_NAME: token, "tags": [str(self.press_releases.pk)]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(self.member.tags.values_list("name", flat=True)),
            {PRESS_RELEASES_TAG_NAME, STAFF_ONLY_TAG_NAME},
            "Only the unticked tag should go: the ticked one and the staff-only one stay.",
        )
        self.assertTrue(
            Engagement.objects.filter(
                person=self.member,
                action_type=Engagement.UNSUBSCRIBED,
                metadata__tag=NEWSLETTER_TAG_NAME,
            ).exists()
        )
        campaign.refresh_from_db()
        self.assertEqual(
            campaign.unsubscription_count,
            1,
            "The one submission should be counted against the campaign whose link was followed.",
        )
        self.assertNotContains(response, f'value="{self.newsletter.pk}"')

    def test_the_token_in_the_hidden_field_is_enough_to_identify_the_recipient(self) -> None:
        # Posting to /unsubscribe without the query string shows that the
        # hidden field, not the address of the page, carries the token.
        membership = self.give_membership()
        token = self.token_for(self.make_campaign())

        self.client.post(self.url, {TOKEN_FIELD_NAME: token, "memberships": []})

        membership.refresh_from_db()
        self.assertFalse(membership.is_current)

    def test_submitting_with_everything_ticked_changes_nothing(self) -> None:
        membership = self.give_membership()
        self.member.tags.add(self.newsletter)
        campaign = self.make_campaign()
        token = self.token_for(campaign)

        self.client.post(
            self.url,
            {
                TOKEN_FIELD_NAME: token,
                "memberships": [str(membership.pk)],
                "tags": [str(self.newsletter.pk)],
            },
        )

        membership.refresh_from_db()
        self.assertTrue(membership.is_current)
        self.assertTrue(self.member.tags.filter(pk=self.newsletter.pk).exists())
        campaign.refresh_from_db()
        self.assertFalse(campaign.unsubscription_count)

    def test_a_tampered_token_changes_nothing(self) -> None:
        membership = self.give_membership()
        self.member.tags.add(self.newsletter)
        tampered_token = self.token_for(self.make_campaign()) + "tampered"

        response = self.client.post(
            self.email_link(tampered_token), {TOKEN_FIELD_NAME: tampered_token}
        )

        self.assertContains(response, INVALID_LINK_TEXT)
        membership.refresh_from_db()
        self.assertTrue(membership.is_current)
        self.assertTrue(self.member.tags.filter(pk=self.newsletter.pk).exists())

    def test_an_answer_to_the_page_s_questions_is_recorded_with_the_recipient_s_address(
        self,
    ) -> None:
        token = self.token_for(self.make_campaign())

        self.client.post(self.url, {TOKEN_FIELD_NAME: token})

        submission = FormSubmission.objects.get()
        self.assertEqual(submission.email_address, self.member.email)
        self.assertFalse(
            submission.is_authenticated,
            "A link from an email is not a login, so the submission is not authenticated.",
        )


class LoggedInTests(UnsubscriptionPageTestCase):
    def test_unticking_a_membership_and_submitting_cancels_it(self) -> None:
        membership = self.give_membership()
        self.client.force_login(self.member)

        response = self.client.post(self.url, {"memberships": []})

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

        response = self.client.post(self.url, {"memberships": [str(membership.pk)]})

        self.assertTrue(
            response.context["form"].errors,
            "Another person's membership is not among the stranger's choices.",
        )
        membership.refresh_from_db()
        self.assertTrue(membership.is_current)

    def test_an_anonymous_visitor_without_a_link_cannot_unsubscribe_anybody(self) -> None:
        membership = self.give_membership()

        response = self.client.post(self.url, {"memberships": []})

        self.assertTrue(response.context["form"].non_field_errors())
        membership.refresh_from_db()
        self.assertTrue(membership.is_current)
        self.assertFalse(FormSubmission.objects.exists())

    def test_a_logged_in_visitor_needs_no_token(self) -> None:
        # The member has kept a link whose token no longer verifies (say, after
        # the signing key was changed), but they are logged in.
        self.member.tags.add(self.newsletter)
        self.client.force_login(self.member)
        stale_token = self.token_for(self.make_campaign()) + "stale"

        response = self.client.post(self.email_link(stale_token), {TOKEN_FIELD_NAME: stale_token})

        self.assertNotContains(response, INVALID_LINK_TEXT)
        self.assertFalse(self.member.tags.filter(pk=self.newsletter.pk).exists())

    def test_a_logged_in_visitor_is_not_mistaken_for_the_person_a_token_names(self) -> None:
        # Somebody logged in follows a link from an email forwarded to them.
        recipient_membership = self.give_membership()
        token = self.token_for(self.make_campaign())
        stranger = Person.objects.create_user(
            email="lucas.nguyen@example.com", first_name="Lucas", last_name="Nguyen"
        )
        self.client.force_login(stranger)

        response = self.client.get(self.email_link(token))

        self.assertEqual(response.context["form"].visitor, stranger)
        self.assertNotContains(response, MEMBERSHIP_TYPE_NAME)
        self.client.post(self.email_link(token), {TOKEN_FIELD_NAME: token, "memberships": []})
        recipient_membership.refresh_from_db()
        self.assertTrue(recipient_membership.is_current)

    def test_a_logged_in_visitor_s_own_token_credits_the_campaign(self) -> None:
        self.member.tags.add(self.newsletter)
        campaign = self.make_campaign()
        token = self.token_for(campaign)
        self.client.force_login(self.member)

        self.client.post(self.url, {TOKEN_FIELD_NAME: token})

        campaign.refresh_from_db()
        self.assertEqual(
            campaign.unsubscription_count,
            1,
            "The token names the logged-in member, so it still says which campaign brought them.",
        )
