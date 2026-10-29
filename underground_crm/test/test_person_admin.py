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


class PersonAdminMembershipInlineTest(django.test.TestCase):
    """
    Covers the Memberships section of the Person change form, which lets staff see
    and edit a person's memberships without leaving their record.
    """

    def setUp(self):
        from datetime import date, datetime
        from zoneinfo import ZoneInfo

        from underground_crm.models import Membership, MembershipType

        self.client.force_login(
            Person.objects.create_user(
                email="admin-viewer@example.com",
                password="password",
                is_staff=True,
                is_superuser=True,
            )
        )
        melbourne = ZoneInfo("Australia/Melbourne")
        self.member = Person.objects.create_user(
            email="jordan.nguyen@example.com", password="password"
        )
        self.pirate_type = MembershipType.objects.create(name="Pirate Party")
        self.fusion_type = MembershipType.objects.create(name="Fusion Party")
        self.pirate_membership = Membership.objects.create(
            person=self.member,
            type=self.pirate_type,
            started_at=datetime(2022, 3, 1, 9, 30, tzinfo=melbourne),
            expires_on=date(2023, 3, 1),
        )
        self.fusion_membership = Membership.objects.create(
            person=self.member,
            type=self.fusion_type,
            started_at=datetime(2024, 7, 15, 14, 0, tzinfo=melbourne),
        )
        self.non_member = Person.objects.create_user(
            email="sam.taylor@example.com", password="password"
        )
        self.url = reverse("admin:underground_crm_person_change", args=[self.member.pk])

    def _membership_formset(self, response):
        from underground_crm.models import Membership

        return next(
            formset
            for formset in response.context["inline_admin_formsets"]
            if formset.formset.model is Membership
        )

    def test_change_form_lists_every_membership_of_the_person(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        listed = {form.instance.pk for form in self._membership_formset(response).formset.forms}
        self.assertEqual(
            listed,
            {self.pirate_membership.pk, self.fusion_membership.pk},
            "The inline should list exactly the memberships that belong to this person, "
            "and no blank rows, since extra=0",
        )

    def test_change_form_omits_memberships_of_other_people(self):
        from underground_crm.models import Membership

        others_membership = Membership.objects.create(
            person=self.non_member,
            type=self.fusion_type,
            started_at=self.fusion_membership.started_at,
        )
        response = self.client.get(self.url)

        listed = {form.instance.pk for form in self._membership_formset(response).formset.forms}
        self.assertNotIn(
            others_membership.pk,
            listed,
            "Another person's membership must not appear on this person's change form",
        )


class PersonAdminHasMembershipFilterTest(django.test.TestCase):
    """
    Covers the "has membership" filter of the Person changelist, which separates
    people holding at least one membership, of any type or status, from everyone else.
    """

    def setUp(self):
        from datetime import date, datetime
        from zoneinfo import ZoneInfo

        from underground_crm.models import Membership, MembershipType

        self.staff_user = Person.objects.create_user(
            email="admin-viewer@example.com",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.staff_user)
        melbourne = ZoneInfo("Australia/Melbourne")
        fusion_type = MembershipType.objects.create(name="Fusion Party")
        self.current_member = Person.objects.create_user(
            email="jordan.nguyen@example.com", password="password"
        )
        Membership.objects.create(
            person=self.current_member,
            type=fusion_type,
            started_at=datetime(2024, 7, 15, 14, 0, tzinfo=melbourne),
        )
        self.expired_member = Person.objects.create_user(
            email="priya.sharma@example.com", password="password"
        )
        Membership.objects.create(
            person=self.expired_member,
            type=fusion_type,
            started_at=datetime(2021, 2, 10, 11, 0, tzinfo=melbourne),
            expires_on=date(2022, 2, 10),
        )
        self.non_member = Person.objects.create_user(
            email="sam.taylor@example.com", password="password"
        )
        self.url = reverse("admin:underground_crm_person_changelist")

    def _listed_pks(self, choice: str) -> set:
        response = self.client.get(self.url, {"has_membership": choice})
        self.assertEqual(response.status_code, 200)
        return {person.pk for person in response.context["cl"].result_list}

    def test_yes_lists_everyone_with_a_membership_even_an_expired_one(self):
        self.assertEqual(
            self._listed_pks("yes"),
            {self.current_member.pk, self.expired_member.pk},
            "The filter asks whether a membership exists at all, so an expired "
            "membership counts just as much as a current one",
        )

    def test_no_lists_everyone_without_a_membership(self):
        self.assertEqual(
            self._listed_pks("no"),
            {self.non_member.pk, self.staff_user.pk},
            "Neither the non-member nor the staff user viewing the page holds a "
            "membership, so both belong in the list",
        )
