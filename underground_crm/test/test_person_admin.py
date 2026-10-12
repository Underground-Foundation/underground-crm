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


class PersonAdminLastInteractionTest(django.test.TestCase):
    """
    Covers the "Last interaction" column of the Person admin changelist, which
    shows (and sorts by) the most recent Interaction of each person.
    """

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from underground_crm.models import Interaction

        self.client.force_login(
            Person.objects.create_user(
                email="admin-viewer@example.com",
                password="password",
                is_staff=True,
                is_superuser=True,
            )
        )
        now = timezone.now()
        self.recent = Person.objects.create_user(email="recent@example.com", password="x")
        self.older = Person.objects.create_user(email="older@example.com", password="x")
        self.never = Person.objects.create_user(email="never@example.com", password="x")
        for person, age in ((self.recent, 1), (self.recent, 30), (self.older, 10)):
            interaction = Interaction.objects.create(person=person, method=Interaction.METHOD_EMAIL)
            Interaction.objects.filter(pk=interaction.pk).update(
                created_at=now - timedelta(days=age)
            )
        self.url = reverse("admin:underground_crm_person_changelist")

    def _ordered_pks(self, order):
        response = self.client.get(self.url, {"o": order})
        self.assertEqual(response.status_code, 200)
        return [person.pk for person in response.context["cl"].result_list]

    def test_changelist_has_last_interaction_column(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Last interaction")

    def test_sorting_by_last_interaction_puts_the_most_recent_first(self):
        from underground_crm.admin import PersonAdmin

        list_display = list(PersonAdmin.list_display)
        column = list_display.index("last_interaction") + 1
        pks = self._ordered_pks(f"-{column}")
        self.assertLess(pks.index(self.recent.pk), pks.index(self.older.pk))
