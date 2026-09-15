import django.test
from django.contrib.auth import get_user_model
from django.urls import reverse

Person = get_user_model()


class PersonAdminEmailDisplayTest(django.test.TestCase):
    """
    Covers masking of email addresses in the Person admin changelist, the same
    protection already applied to the People Filter evaluation screen.
    """

    def setUp(self):
        self.staff_user = Person.objects.create_user(
            email="admin-viewer@example.com",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.staff_user)

        self.listed_email = "owen9825@gmail.com"
        self.listed_person = Person.objects.create_user(
            email=self.listed_email, password="password"
        )
        self.changelist_url = reverse("admin:underground_crm_person_changelist")

    def test_changelist_shows_the_masked_email_address(self):
        response = self.client.get(self.changelist_url)

        self.assertContains(
            response,
            self.listed_person.partial_email_address,
            msg_prefix="The Person changelist should show the masked email address, "
            "matching the protection already applied to the People Filter evaluation "
            "screen",
        )

    def test_changelist_never_shows_the_full_email_address(self):
        response = self.client.get(self.changelist_url)

        self.assertNotContains(
            response,
            self.listed_email,
            msg_prefix="The full, unmasked email address must never be rendered on the "
            "Person changelist, since an unauthorized person could be looking at the "
            "screen",
        )
