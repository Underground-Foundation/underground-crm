"""
Tests for the SubscriptionPage, which migration 0018 puts at /unsubscribe/.

The page is about whoever is logged in, or else whoever the signed token in an
email's unsubscription link names. Its form lists their current memberships,
each with a ticked checkbox, and every tag named in SELF_QUERYABLE_TAGS, ticked
if they hold it. Submitting it with a membership unticked cancels it; ticking
or unticking a tag subscribes or unsubscribes them. Following the link changes
nothing by itself, because over-eager email clients follow links that nobody
clicked.

Every site is given the page by migration 0018, so these tests use the page
that the migrated test database already has, rather than creating one.

Expected texts are written out as constants rather than copied from the
template, so that a test fails if the wording a visitor reads changes; the
suite runs in the site's default language (English), so they are not
translated.
"""

import datetime
from http import HTTPStatus

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
from underground_crm.forms.form_submission import _field_name
from underground_crm.models.pages import FormPage
from underground_crm.test.form_inputs import typed_input
from underground_email.models import (
    EmailCampaign,
    EmailSender,
    SubscriptionPage,
    SubscriptionPageSubmission,
)
from underground_email.unsubscription import (
    generate_unsubscription_signature,
    make_unsubscription_url,
    subscription_page_url,
)
from underground_email.subscription_form import TOKEN_FIELD_NAME

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

# Where migration 0018 puts the page, beneath the default site's root page.
UNSUBSCRIPTION_PATH = "/unsubscribe/"
NOT_A_MEMBER_TEXT = "You are not a member."
NOT_SUBSCRIBED_TEXT = "You are not subscribed to the newsletter."
INVALID_LINK_TEXT = "This unsubscription link is not valid."
SUBMIT_BUTTON_TEXT = "Update subscriptions"
# A question an editor might place on the page.
REASON_QUESTION = "What made you decide to unsubscribe?"
REASON_ANSWER = "I was getting more emails than I could read."
DEPARTURE_NOTES_PROMPT = "Sorry to see you go. Feel free to leave any notes about why this is:"


@override_settings(SELF_QUERYABLE_TAGS=SELF_QUERYABLE_TAG_NAMES)
class SubscriptionPageTestCase(django.test.TestCase):
    def setUp(self) -> None:
        self.url = SubscriptionPage.objects.get().url
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


class MigrationTests(SubscriptionPageTestCase):
    def test_every_site_is_given_a_live_subscription_page(self) -> None:
        for site in Site.objects.all():
            self.assertTrue(
                SubscriptionPage.objects.live()
                .descendant_of(site.root_page, inclusive=True)
                .exists(),
                f"Migration 0018 should have given {site} a subscription page.",
            )

    def test_the_page_tree_is_intact(self) -> None:
        # The migration writes treebeard's materialized paths by hand, since
        # historical models lack the tree-editing methods.
        problems = Page.find_problems()
        self.assertFalse(any(problems), f"Treebeard found problems in the page tree: {problems}")

    def test_the_page_is_at_unsubscribe_beneath_the_site_root(self) -> None:
        self.assertEqual(self.url, UNSUBSCRIPTION_PATH)

    def test_old_links_without_the_slash_keep_their_token(self) -> None:
        # Emails sent before the page existed link to /unsubscribe, which
        # Django's APPEND_SLASH redirects to the page.
        token = self.token_for(self.make_campaign())
        query_string = f"{TOKEN_FIELD_NAME}={token}"

        response = self.client.get(f"{UNSUBSCRIPTION_PATH.rstrip('/')}?{query_string}")

        self.assertRedirects(
            response,
            f"{UNSUBSCRIPTION_PATH}?{query_string}",
            status_code=HTTPStatus.MOVED_PERMANENTLY,  # APPEND_SLASH's redirect is permanent
            fetch_redirect_response=False,
        )

    def test_an_email_links_to_the_page(self) -> None:
        campaign = self.make_campaign()
        site = Site.objects.get(is_default_site=True)

        link = make_unsubscription_url(self.member, campaign.utm_id, subscription_page_url(site))

        self.assertTrue(
            link.startswith(f"{site.root_url}{UNSUBSCRIPTION_PATH}?"),
            f"The link should lead to the page that migration 0018 created: {link}",
        )


class ListingTests(SubscriptionPageTestCase):
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
        self.assertContains(response, SUBMIT_BUTTON_TEXT)

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


class EmailLinkTests(SubscriptionPageTestCase):
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
        self.assertEqual(
            list(response.context["form"]["tags"].value()),
            [self.press_releases.pk],
            "The page shown afterwards should have only the kept tag ticked.",
        )

    def test_the_token_in_the_hidden_field_is_enough_to_identify_the_recipient(self) -> None:
        # Posting to /unsubscribe/ without the query string shows that the
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
        page = SubscriptionPage.objects.get()
        page.body = [("text", typed_input(REASON_QUESTION))]
        page.save()
        page.refresh_from_db()
        (reason_input,) = page.inputs
        token = self.token_for(self.make_campaign())

        self.client.post(
            self.url,
            {TOKEN_FIELD_NAME: token, _field_name(reason_input): REASON_ANSWER},
        )

        submission = FormSubmission.objects.get()
        self.assertEqual(
            submission.submitted_fields.get(block_id=str(reason_input.id)).value,
            REASON_ANSWER,
        )
        self.assertEqual(submission.email_address, self.member.email)
        self.assertFalse(
            submission.is_authenticated,
            "A link from an email is not a login, so the submission is not authenticated.",
        )


class LoggedInTests(SubscriptionPageTestCase):
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

    def test_an_anonymous_visitor_without_a_link_cannot_change_anybody_s_subscriptions(
        self,
    ) -> None:
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


class SubscribingTests(SubscriptionPageTestCase):
    def test_ticking_an_unticked_tag_subscribes_the_visitor(self) -> None:
        self.member.tags.add(self.newsletter)
        self.member.email_opt_in = False
        self.member.save(update_fields=["email_opt_in"])
        token = self.token_for(self.make_campaign())

        response = self.client.get(self.email_link(token))
        self.assertEqual(
            list(response.context["form"]["tags"].value()),
            [self.newsletter.pk],
            "A tag the visitor does not hold is offered, unticked.",
        )
        self.assertContains(response, PRESS_RELEASES_TAG_NAME)

        self.client.post(
            self.url,
            {
                TOKEN_FIELD_NAME: token,
                "tags": [str(self.newsletter.pk), str(self.press_releases.pk)],
            },
        )

        self.assertEqual(
            set(self.member.tags.values_list("name", flat=True)),
            {NEWSLETTER_TAG_NAME, PRESS_RELEASES_TAG_NAME},
        )
        self.member.refresh_from_db()
        self.assertTrue(
            self.member.email_opt_in,
            "Subscribing to a mailing list is asking for email, as on the subscription endpoint.",
        )

    def test_a_staff_only_tag_is_never_offered(self) -> None:
        self.client.force_login(self.member)

        response = self.client.post(self.url, {"tags": [str(self.staff_only_tag.pk)]})

        self.assertTrue(response.context["form"].errors)
        self.assertFalse(self.member.tags.filter(pk=self.staff_only_tag.pk).exists())

    def test_the_not_subscribed_box_accompanies_the_tags_on_offer(self) -> None:
        self.client.force_login(self.member)

        response = self.client.get(self.url)

        self.assertContains(response, NOT_SUBSCRIBED_TEXT)
        self.assertContains(response, NEWSLETTER_TAG_NAME)


class SignupFieldTests(SubscriptionPageTestCase):
    def test_an_anonymous_visitor_is_never_asked_who_they_are(self) -> None:
        response = self.client.get(self.url)

        for field_name in ("email", "first_name", "last_name"):
            self.assertNotIn(
                field_name,
                response.context["form"].fields,
                "A typed address proves nothing, so the page must not ask for one.",
            )

    def test_an_ordinary_form_page_still_asks_anonymous_visitors_who_they_are(self) -> None:
        self.assertTrue(FormPage.should_request_signup_fields)
        self.assertFalse(SubscriptionPage.should_request_signup_fields)


class DepartureNotesTests(SubscriptionPageTestCase):
    def test_the_notes_box_is_offered_beneath_its_prompt(self) -> None:
        self.give_membership()
        self.client.force_login(self.member)

        response = self.client.get(self.url)

        self.assertContains(response, DEPARTURE_NOTES_PROMPT)
        self.assertContains(response, 'name="departure_notes"')

    def test_notes_left_when_cancelling_are_saved_with_the_submission(self) -> None:
        notes = "I have moved to Hobart and joined the local branch there instead."
        membership = self.give_membership()
        token = self.token_for(self.make_campaign())

        self.client.post(
            self.url, {TOKEN_FIELD_NAME: token, "memberships": [], "departure_notes": notes}
        )

        membership.refresh_from_db()
        self.assertFalse(membership.is_current)
        submission = SubscriptionPageSubmission.objects.get()
        self.assertEqual(submission.departure_notes, notes)
        self.assertEqual(submission.email_address, self.member.email)

    def test_without_notes_or_answers_no_submission_is_saved(self) -> None:
        self.member.tags.add(self.newsletter)
        self.client.force_login(self.member)

        self.client.post(self.url, {"tags": []})

        self.assertFalse(
            self.member.tags.filter(pk=self.newsletter.pk).exists(),
            "The unsubscription itself must happen whether or not a submission is saved.",
        )
        self.assertFalse(
            FormSubmission.objects.exists(),
            "With nothing written and no question answered, there is nothing to record.",
        )
