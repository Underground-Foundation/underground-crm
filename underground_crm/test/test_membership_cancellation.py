import datetime

import django.test
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from wagtail.models import Site

from underground_crm.models.membership import Membership, MembershipType
from underground_crm.models.pages import RegistrationPage

Person = get_user_model()

PASSWORD = "correct-horse-battery"
# Deliberately not "Fusion Party": these tests run against the Fusion theme,
# whose branding puts that name in the page's own template elements (the
# <title>, the nav), which would make a membership-type name of "Fusion
# Party" match incidentally rather than because the memberships section
# rendered it.
MEMBERSHIP_TYPE_NAME = "Pirate Party"
# Far enough in the future that "still active" is unambiguous, however long
# the test suite takes to run.
FUTURE_EXPIRY = timezone.now().date() + datetime.timedelta(days=365)
# Far enough in the past that "already expired" is unambiguous.
PAST_EXPIRY = timezone.now().date() - datetime.timedelta(days=1)
UNSAFE_NEXT_URL = "https://attacker.example.com/phishing/"


class CancelMembershipViewTest(django.test.TestCase):
    def setUp(self):
        self.member = Person.objects.create_user(
            email="priya.sharma@example.com", password=PASSWORD
        )
        self.membership_type = MembershipType.objects.create(name=MEMBERSHIP_TYPE_NAME)
        self.membership = Membership.objects.create(
            person=self.member,
            type=self.membership_type,
            started_at=timezone.now(),
            expires_on=FUTURE_EXPIRY,
        )
        self.url = reverse(
            "underground_crm:cancel_membership", kwargs={"membership_id": self.membership.pk}
        )

    def test_cancel_sets_expiry_to_today(self):
        self.client.force_login(self.member)
        self.client.post(self.url)

        self.membership.refresh_from_db()
        self.assertEqual(
            self.membership.expires_on,
            timezone.now().date(),
            "Cancelling a membership must bring its expiry forward to today, "
            "rather than leaving the future expiry date in place.",
        )

    def test_success_message_names_the_membership_type(self):
        self.client.force_login(self.member)
        response = self.client.post(self.url, follow=True)

        messages = [str(message) for message in response.context["messages"]]
        self.assertTrue(
            any(MEMBERSHIP_TYPE_NAME in message for message in messages),
            f"The confirmation toast must name the cancelled membership type "
            f"so the visitor knows which membership was affected: {messages!r}",
        )

    def test_redirects_to_next_when_it_is_a_local_url(self):
        self.client.force_login(self.member)
        local_next = "/become-a-member/"
        response = self.client.post(self.url, {"next": local_next})
        self.assertRedirects(response, local_next, fetch_redirect_response=False)

    def test_ignores_an_off_site_next_url(self):
        self.client.force_login(self.member)
        response = self.client.post(self.url, {"next": UNSAFE_NEXT_URL})
        self.assertNotEqual(
            response.url,
            UNSAFE_NEXT_URL,
            "An attacker-supplied next URL must never be followed, or this "
            "endpoint becomes an open redirect.",
        )

    def test_cannot_cancel_someone_elses_membership(self):
        other_member = Person.objects.create_user(
            email="lucas.nguyen@example.com", password=PASSWORD
        )
        self.client.force_login(other_member)
        response = self.client.post(self.url)

        self.assertEqual(
            response.status_code,
            404,
            "A membership id alone must not be enough to cancel it — it must "
            "also belong to the requesting visitor.",
        )
        self.membership.refresh_from_db()
        self.assertEqual(
            self.membership.expires_on,
            FUTURE_EXPIRY,
            "The other visitor's rejected request must leave the membership " "untouched.",
        )

    def test_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.post(self.url)
        self.assertEqual(
            response.status_code,
            302,
            "An anonymous visitor must be sent to log in rather than being "
            "allowed to cancel a membership.",
        )
        self.assertIn("/account/login/", response.url)

    def test_get_request_is_rejected(self):
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertEqual(
            response.status_code,
            405,
            "Cancelling a membership changes data, so it must only be " "reachable by POST.",
        )


class RegistrationPageMembershipsTest(django.test.TestCase):
    def setUp(self):
        home_page = Site.objects.get(is_default_site=True).root_page
        self.page = RegistrationPage(title="Become a member", slug="become-a-member")
        self.page.body = [
            ("person_field", {"field": "preferred_name", "label_override": ""}),
        ]
        home_page.add_child(instance=self.page)

        self.member = Person.objects.create_user(
            email="priya.sharma@example.com", password=PASSWORD
        )
        self.membership_type = MembershipType.objects.create(name=MEMBERSHIP_TYPE_NAME)

    def test_active_membership_shows_a_cancel_button(self):
        Membership.objects.create(
            person=self.member,
            type=self.membership_type,
            started_at=timezone.now(),
            expires_on=FUTURE_EXPIRY,
        )
        self.client.force_login(self.member)
        response = self.client.get(self.page.url)

        self.assertContains(response, MEMBERSHIP_TYPE_NAME)
        self.assertContains(response, "Cancel membership")

    def test_expired_membership_shows_no_cancel_button(self):
        Membership.objects.create(
            person=self.member,
            type=self.membership_type,
            started_at=timezone.now(),
            expires_on=PAST_EXPIRY,
        )
        self.client.force_login(self.member)
        response = self.client.get(self.page.url)

        self.assertContains(response, MEMBERSHIP_TYPE_NAME)
        self.assertNotContains(
            response,
            "Cancel membership",
            msg_prefix="An already-expired membership has nothing left to cancel.",
        )

    def test_anonymous_visitor_sees_no_memberships_section(self):
        Membership.objects.create(
            person=self.member,
            type=self.membership_type,
            started_at=timezone.now(),
            expires_on=FUTURE_EXPIRY,
        )
        response = self.client.get(self.page.url)
        self.assertNotContains(
            response,
            MEMBERSHIP_TYPE_NAME,
            msg_prefix="Membership records must never leak to a signed-out visitor.",
        )

    def test_cancelling_shows_a_toast_on_return_to_the_page(self):
        membership = Membership.objects.create(
            person=self.member,
            type=self.membership_type,
            started_at=timezone.now(),
            expires_on=FUTURE_EXPIRY,
        )
        self.client.force_login(self.member)
        cancel_url = reverse(
            "underground_crm:cancel_membership", kwargs={"membership_id": membership.pk}
        )

        response = self.client.post(cancel_url, {"next": self.page.url}, follow=True)

        self.assertContains(response, "toast-notification")
        self.assertContains(response, MEMBERSHIP_TYPE_NAME)


ADD_MEMBERSHIP_BUTTON_TEXT = f"Add {MEMBERSHIP_TYPE_NAME} membership"


class AddMembershipTest(django.test.TestCase):
    """The "Add membership" button on a RegistrationPage, and the view it posts to."""

    def setUp(self):
        home_page = Site.objects.get(is_default_site=True).root_page
        self.membership_type = MembershipType.objects.create(name=MEMBERSHIP_TYPE_NAME)
        self.page = RegistrationPage(
            title="Become a member", slug="become-a-member", membership=self.membership_type
        )
        self.page.body = [
            ("person_field", {"field": "preferred_name", "label_override": ""}),
        ]
        home_page.add_child(instance=self.page)
        self.url = reverse("underground_crm:add_membership", kwargs={"page_id": self.page.pk})

        self.member = Person.objects.create_user(
            email="priya.sharma@example.com", password=PASSWORD
        )

    def _create_membership(self, expires_on: datetime.date, is_approved: bool = True) -> Membership:
        return Membership.objects.create(
            person=self.member,
            type=self.membership_type,
            started_at=timezone.now(),
            expires_on=expires_on,
            is_approved=is_approved,
        )

    def test_button_is_offered_to_a_visitor_without_a_membership(self):
        self.client.force_login(self.member)
        response = self.client.get(self.page.url)
        self.assertContains(response, ADD_MEMBERSHIP_BUTTON_TEXT)

    def test_button_is_offered_again_after_a_membership_has_expired(self):
        self._create_membership(expires_on=PAST_EXPIRY)
        self.client.force_login(self.member)
        response = self.client.get(self.page.url)
        self.assertContains(
            response,
            ADD_MEMBERSHIP_BUTTON_TEXT,
            msg_prefix="A lapsed member must be able to rejoin, since resubmitting "
            "the form never grants a type they have held before.",
        )

    def test_button_is_not_offered_to_an_active_member(self):
        self._create_membership(expires_on=FUTURE_EXPIRY)
        self.client.force_login(self.member)
        response = self.client.get(self.page.url)
        self.assertNotContains(response, ADD_MEMBERSHIP_BUTTON_TEXT)

    def test_button_is_not_offered_to_an_applicant_awaiting_approval(self):
        self._create_membership(expires_on=FUTURE_EXPIRY, is_approved=False)
        self.client.force_login(self.member)
        response = self.client.get(self.page.url)
        self.assertNotContains(
            response,
            ADD_MEMBERSHIP_BUTTON_TEXT,
            msg_prefix="Nobody may hold two memberships of the same type, and an "
            "application awaiting approval already is one.",
        )
        self.assertContains(response, "awaiting approval")

    def test_button_is_not_offered_when_the_page_grants_no_membership(self):
        self.page.membership = None
        self.page.save()
        self.client.force_login(self.member)
        response = self.client.get(self.page.url)
        self.assertNotContains(response, ADD_MEMBERSHIP_BUTTON_TEXT)

    def test_adding_applies_for_the_pages_membership_type(self):
        self.client.force_login(self.member)
        self.client.post(self.url)
        self.assertTrue(
            self.member.memberships.awaiting_approval().filter(type=self.membership_type).exists(),
            "Pressing the button must apply for the membership type the page offers, "
            "and the application must wait for staff to approve it.",
        )

    def test_rejoining_keeps_the_expired_membership_as_a_record(self):
        expired = self._create_membership(expires_on=PAST_EXPIRY)
        self.client.force_login(self.member)
        self.client.post(self.url)

        self.assertTrue(
            Membership.objects.filter(pk=expired.pk, expires_on=PAST_EXPIRY).exists(),
            "The earlier term must be kept unchanged as a record of past membership.",
        )
        self.assertTrue(
            self.member.memberships.awaiting_approval().filter(type=self.membership_type).exists(),
            "A new membership must be started beside the expired one.",
        )

    def test_adding_twice_creates_only_one_membership(self):
        self.client.force_login(self.member)
        self.client.post(self.url)
        self.client.post(self.url)
        self.assertEqual(
            self.member.memberships.filter(type=self.membership_type).count(),
            1,
            "A double click must not leave the visitor with two memberships of " "the same type.",
        )

    def test_adding_shows_a_toast_on_return_to_the_page(self):
        self.client.force_login(self.member)
        response = self.client.post(self.url, {"next": self.page.url}, follow=True)
        self.assertContains(response, "toast-notification")
        self.assertContains(response, MEMBERSHIP_TYPE_NAME)

    def test_ignores_an_off_site_next_url(self):
        self.client.force_login(self.member)
        response = self.client.post(self.url, {"next": UNSAFE_NEXT_URL})
        self.assertNotEqual(
            response.url,
            UNSAFE_NEXT_URL,
            "An attacker-supplied next URL must never be followed, or this "
            "endpoint becomes an open redirect.",
        )

    def test_unpublished_page_grants_nothing(self):
        self.page.unpublish()
        self.client.force_login(self.member)
        response = self.client.post(self.url)
        self.assertEqual(
            response.status_code,
            404,
            "Only a live page may grant a membership, so withdrawing a page "
            "also withdraws its offer.",
        )
        self.assertFalse(self.member.memberships.exists())

    def test_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.post(self.url)
        self.assertEqual(
            response.status_code,
            302,
            "An anonymous visitor must be sent to log in, since a membership "
            "needs a Person to belong to.",
        )
        self.assertIn("/account/login/", response.url)
        self.assertFalse(Membership.objects.exists())

    def test_get_request_is_rejected(self):
        self.client.force_login(self.member)
        response = self.client.get(self.url)
        self.assertEqual(
            response.status_code,
            405,
            "Adding a membership changes data, so it must only be reachable by POST.",
        )
