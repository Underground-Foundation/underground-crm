"""
Tests for which users can be chosen as an email sender.

Emails are sent from the chosen user's address through SMTP2Go, which only accepts
domains that have been registered with it.  The organisation's own domains are the ones
in ALLOWED_HOSTS, so staff and users at those domains are the plausible senders; offering
every member of the public would invite attributing mail to an address we cannot send as.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from underground_email.forms import sender_candidates

ALLOWED_HOSTS = ["votefusion.org", ".fusionparty.org.au", "*", "localhost"]


class SenderCandidatesTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        user_model = get_user_model()

        def make(email: str, is_staff: bool = False):
            return user_model.objects.create(email=email, is_staff=is_staff)

        cls.staff_on_personal_address = make("marisol.ortega@gmail.com", is_staff=True)
        cls.member_at_own_domain = make("hello@votefusion.org")
        cls.member_at_wildcard_subdomain = make("vic@branches.fusionparty.org.au")
        cls.member_at_wildcard_bare_domain = make("treasurer@fusionparty.org.au")
        cls.member_with_uppercase_domain = make("Dev.Patel@VoteFusion.org")
        cls.ordinary_member = make("tomasz.nowak@outlook.com")
        cls.lookalike_domain = make("scam@notvotefusion.org")
        cls.lookalike_of_wildcard = make("scam@evilfusionparty.org.au")

    def candidates(self, **kwargs) -> set:
        return set(sender_candidates(ALLOWED_HOSTS, **kwargs))

    def test_staff_are_offered_whatever_their_address(self) -> None:
        self.assertIn(self.staff_on_personal_address, self.candidates())

    def test_users_at_an_allowed_host_are_offered_ignoring_case(self) -> None:
        self.assertIn(self.member_at_own_domain, self.candidates())
        self.assertIn(self.member_with_uppercase_domain, self.candidates())

    def test_leading_dot_host_matches_the_bare_domain_and_its_subdomains(self) -> None:
        self.assertIn(self.member_at_wildcard_subdomain, self.candidates())
        self.assertIn(self.member_at_wildcard_bare_domain, self.candidates())

    def test_other_members_are_not_offered(self) -> None:
        self.assertNotIn(self.ordinary_member, self.candidates())

    def test_a_domain_merely_ending_in_an_allowed_host_is_not_offered(self) -> None:
        self.assertNotIn(self.lookalike_domain, self.candidates(), "must match at the @")
        self.assertNotIn(
            self.lookalike_of_wildcard, self.candidates(), "must match at a dot boundary"
        )

    def test_the_current_sender_stays_selectable_when_editing(self) -> None:
        self.assertIn(self.ordinary_member, self.candidates(keep_pk=self.ordinary_member.pk))
