import django.test
from django.urls import reverse


class PasswordResetPageTest(django.test.TestCase):
    def test_password_reset_page_uses_site_layout(self) -> None:
        response = self.client.get(reverse("account_reset_password"))
        self.assertEqual(
            response.status_code,
            200,
            "The password reset page is public and must render for an anonymous visitor.",
        )
        self.assertTemplateUsed(
            response,
            "underground_crm/base.html",
            msg_prefix=(
                "django-allauth's own layout is a bare HTML page, so the password reset "
                "page must be rendered through the site's base template instead"
            ),
        )
        self.assertContains(
            response,
            'class="form-control',
            msg_prefix="The email field must carry the Bootstrap class that the login form's fields carry",
        )

    def test_invalid_email_is_marked_invalid(self) -> None:
        response = self.client.post(
            reverse("account_reset_password"), {"email": "not an email address"}
        )
        self.assertContains(
            response,
            "is-invalid",
            msg_prefix="A field that fails validation must be marked for Bootstrap to show its error",
        )
