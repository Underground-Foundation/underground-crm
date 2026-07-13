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
# whose branding puts that name in the page chrome itself (the <title>, the
# nav), which would make a membership-type name of "Fusion Party" match
# incidentally rather than because the memberships section rendered it.
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
