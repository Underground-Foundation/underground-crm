import django.test
from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils.http import urlencode

from underground_crm.contactability import UNDELIVERABLE_EMAIL_MESSAGE

Person = get_user_model()

# Signing up checks that the domain really accepts email, which example.com does not.
MEMBER_EMAIL = "priya.sharma@fusionparty.org.au"
MEMBER_PASSWORD = "correct-horse-battery"
# The real DNS records of example.com declare that it accepts no email, so this
# address is well formed yet undeliverable, like one with a mistyped domain.
UNDELIVERABLE_EMAIL = "priya.sharma@example.com"
HOME_PAGE_URL = "/"
REGISTRATION_PAGE_PATH = "/become-a-member/"
# A target with its own query string: unless it is percent-encoded when copied
# into a link, "source" would become a parameter of that link rather than of `next`.
TARGET_WITH_QUERY_STRING = "/become-a-member/?source=newsletter"
ENCODED_TARGET_WITH_QUERY_STRING = "/become-a-member/%3Fsource%3Dnewsletter"
UNSAFE_NEXT_URLS = {
    "another site": "https://phishing.example.com/login/",
    "a protocol-relative URL, which browsers resolve to another site": (
        "//phishing.example.com/login/"
    ),
    "a backslashed URL, which browsers treat as protocol-relative": (
        "/\\phishing.example.com/login/"
    ),
    "a script, which would run in the context of this site": "javascript:alert(document.cookie)",
}


class LoginPageTest(django.test.TestCase):
    def test_login_page_links_to_password_reset(self) -> None:
        response = self.client.get(reverse("underground_crm:login"))
        self.assertEqual(
            response.status_code,
            200,
            "The login page is public and must render for an anonymous visitor.",
        )
        self.assertContains(
            response,
            f'href="{reverse("account_reset_password")}"',
            msg_prefix=(
                "A member who has forgotten their password has no other way to "
                "reach the reset form, so the login page must link to it"
            ),
        )


class LoginRedirectionTest(django.test.TestCase):
    def setUp(self) -> None:
        Person.objects.create_user(email=MEMBER_EMAIL, password=MEMBER_PASSWORD)

    def _assert_login_redirects(self, *, next_url: str, expected_url: str) -> None:
        response = self.client.post(
            reverse("underground_crm:login"),
            data={"email": MEMBER_EMAIL, "password": MEMBER_PASSWORD},
            # Passed in the query string, because that is where the login form
            # submits it: the form has no action, so it posts back to its own URL.
            query_params={"next": next_url},
        )
        self.assertRedirects(
            response,
            expected_url,
            fetch_redirect_response=False,
            msg_prefix=f"Logging in with next={next_url!r}",
        )

    def test_login_returns_to_a_page_on_this_site(self) -> None:
        self._assert_login_redirects(
            next_url=REGISTRATION_PAGE_PATH, expected_url=REGISTRATION_PAGE_PATH
        )

    def test_login_refuses_to_leave_this_site(self) -> None:
        for description, next_url in UNSAFE_NEXT_URLS.items():
            with self.subTest(description):
                self.client.logout()
                self._assert_login_redirects(next_url=next_url, expected_url=HOME_PAGE_URL)


class SignupRedirectionTest(django.test.TestCase):
    def _assert_signup_redirects(self, *, email: str, next_url: str, expected_url: str) -> None:
        response = self.client.post(
            reverse("underground_crm:signup"),
            data={
                "email": email,
                "preferred_name": "Priya",
                "last_name": "Sharma",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
            # As with login, the signup form posts back to its own URL.
            query_params={"next": next_url},
        )
        self.assertRedirects(
            response,
            expected_url,
            fetch_redirect_response=False,
            msg_prefix=f"Signing up with next={next_url!r}",
        )

    def test_signup_logs_the_new_member_in(self) -> None:
        self._assert_signup_redirects(
            email=MEMBER_EMAIL, next_url=HOME_PAGE_URL, expected_url=HOME_PAGE_URL
        )
        self.assertEqual(
            self.client.session[SESSION_KEY],
            str(Person.objects.get(email=MEMBER_EMAIL).pk),
            "Signing up must leave the new member logged in as themselves, with "
            "no second step of logging in using the password they just chose.",
        )

    def test_signup_returns_to_a_page_on_this_site(self) -> None:
        self._assert_signup_redirects(
            email=MEMBER_EMAIL,
            next_url=REGISTRATION_PAGE_PATH,
            expected_url=REGISTRATION_PAGE_PATH,
        )

    def test_signup_refuses_to_leave_this_site(self) -> None:
        for index, (description, next_url) in enumerate(UNSAFE_NEXT_URLS.items()):
            with self.subTest(description):
                self.client.logout()
                self._assert_signup_redirects(
                    # Each attempt really creates an account, so each needs its own address.
                    email=f"priya.sharma+{index}@fusionparty.org.au",
                    next_url=next_url,
                    expected_url=HOME_PAGE_URL,
                )


class SignupWithUndeliverableEmailTest(django.test.TestCase):
    def test_form_is_shown_again_with_an_error(self) -> None:
        response = self.client.post(
            reverse("underground_crm:signup"),
            data={
                "email": UNDELIVERABLE_EMAIL,
                "preferred_name": "Priya",
                "last_name": "Sharma",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
        )
        self.assertContains(
            response,
            UNDELIVERABLE_EMAIL_MESSAGE,
            msg_prefix="A mistyped domain is the visitor's to correct, not a server error",
        )
        self.assertFalse(
            Person.objects.filter(email=UNDELIVERABLE_EMAIL).exists(),
            "An address that cannot receive email must not become an account.",
        )


class NextUrlSurvivesSwitchingPagesTest(django.test.TestCase):
    """A visitor who arrives at the wrong one of the login and signup pages must
    not lose their destination by following the link to the other."""

    def test_login_page_passes_next_to_its_signup_link(self) -> None:
        response = self.client.get(
            reverse("underground_crm:login"), query_params={"next": TARGET_WITH_QUERY_STRING}
        )
        self.assertContains(
            response,
            f'href="{reverse("underground_crm:signup")}?next={ENCODED_TARGET_WITH_QUERY_STRING}"',
        )

    def test_signup_page_passes_next_to_its_login_link(self) -> None:
        response = self.client.get(
            reverse("underground_crm:signup"), query_params={"next": TARGET_WITH_QUERY_STRING}
        )
        self.assertContains(
            response,
            f'href="{reverse("underground_crm:login")}?next={ENCODED_TARGET_WITH_QUERY_STRING}"',
        )

    def test_links_carry_no_empty_next_when_there_is_none(self) -> None:
        response = self.client.get(reverse("underground_crm:login"))
        self.assertContains(response, f'href="{reverse("underground_crm:signup")}"')


@override_settings(
    ENABLED_SOCIAL_PROVIDERS=["discord"],
    SOCIALACCOUNT_PROVIDERS={
        "discord": {"APPS": [{"client_id": "1234567890", "secret": "discord-client-secret"}]}
    },
)
class SocialButtonsCarryNextTest(django.test.TestCase):
    """The templates pass no `next` to provider_login_url, because allauth's tag
    copies it from the current request. These tests hold allauth to that, since
    a visitor using a social button would otherwise silently lose their destination."""

    def _assert_page_links_discord_login_with_next(self, page_url: str) -> None:
        response = self.client.get(page_url, query_params={"next": REGISTRATION_PAGE_PATH})
        query_string = urlencode({"next": REGISTRATION_PAGE_PATH})
        self.assertContains(response, f'href="{reverse("discord_login")}?{query_string}"')

    def test_login_page_button_carries_next(self) -> None:
        self._assert_page_links_discord_login_with_next(reverse("underground_crm:login"))

    def test_signup_page_button_carries_next(self) -> None:
        self._assert_page_links_discord_login_with_next(reverse("underground_crm:signup"))
