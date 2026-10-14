import django.test
from django.urls import reverse


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
