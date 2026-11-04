import datetime
import json
import zoneinfo

import django.test
from django.contrib.auth import get_user_model
from django.urls import reverse

from underground_crm.forms.person_filter import QueryBuilderWidget
from underground_crm.models import Membership, MembershipType, PeopleFilter
from underground_crm.models.filter import (
    LOGIC_ALL,
    LOGIC_NONE,
    MEMBERSHIP_FIELD,
    MEMBERSHIP_HELD_OPERATOR,
    MEMBERSHIP_SINCE_KEY,
    MEMBERSHIP_SINCE_OPERATOR,
)

Person = get_user_model()

MELBOURNE = zoneinfo.ZoneInfo("Australia/Melbourne")

# The cutoff is early in the morning in Melbourne, when it is still the previous day
# in UTC, so a cutoff read as UTC instead of as the site's time zone would be ten hours
# late and would wrongly exclude the person who joined exactly on it.
CUTOFF = datetime.datetime(2026, 7, 1, 8, 30, tzinfo=MELBOURNE)
# The query builder's datetime-local input submits this form, with no UTC offset.
CUTOFF_AS_SUBMITTED = "2026-07-01T08:30"
ONE_MINUTE = datetime.timedelta(minutes=1)


def _membership_rule(membership_type: MembershipType) -> dict:
    return {
        "field": MEMBERSHIP_FIELD,
        "operator": MEMBERSHIP_HELD_OPERATOR,
        "value": str(membership_type.pk),
    }


def _membership_since_rule(membership_type: MembershipType, since: str) -> dict:
    return {
        "field": MEMBERSHIP_FIELD,
        "operator": MEMBERSHIP_SINCE_OPERATOR,
        "value": str(membership_type.pk),
        MEMBERSHIP_SINCE_KEY: since,
    }


def _matched_emails(logic: str, *rules: dict) -> set[str]:
    people_filter = PeopleFilter.objects.create(
        name="Members under test", criteria={"logic": logic, "rules": list(rules)}
    )
    return set(people_filter.apply(Person.objects.all()).values_list("email", flat=True))


class MembershipFilterTest(django.test.TestCase):
    def setUp(self):
        self.fusion, _ = MembershipType.objects.get_or_create(name="Fusion Party")
        self.pirate, _ = MembershipType.objects.get_or_create(name="Pirate Party")

        self.non_member = Person.objects.create_user(
            email="harper.nonmember@example.com", password="password"
        )
        self.old_fusion_member = self._member("quinn.oldfusion@example.com")
        self.cutoff_fusion_member = self._member("rowan.cutoff@example.com")
        self.pirate_member = self._member("ellis.pirate@example.com")

        self._join(self.old_fusion_member, self.fusion, CUTOFF - ONE_MINUTE)
        self._join(self.cutoff_fusion_member, self.fusion, CUTOFF)
        self._join(self.pirate_member, self.pirate, CUTOFF + ONE_MINUTE)

    @staticmethod
    def _member(email: str) -> Person:
        return Person.objects.create_user(email=email, password="password")

    @staticmethod
    def _join(
        person: Person, membership_type: MembershipType, started_at: datetime.datetime
    ) -> None:
        Membership.objects.create(person=person, type=membership_type, started_at=started_at)

    def test_has_membership_matches_only_people_with_that_type(self):
        self.assertEqual(
            _matched_emails(LOGIC_ALL, _membership_rule(self.fusion)),
            {self.old_fusion_member.email, self.cutoff_fusion_member.email},
        )

    def test_has_membership_since_includes_the_exact_time_and_excludes_earlier(self):
        self.assertEqual(
            _matched_emails(LOGIC_ALL, _membership_since_rule(self.fusion, CUTOFF_AS_SUBMITTED)),
            {self.cutoff_fusion_member.email},
            f"{self.old_fusion_member.email} joined a minute before the cutoff, and "
            f"{self.pirate_member.email} joined after it but holds a different type.",
        )

    def test_since_requires_one_membership_to_satisfy_both_clauses(self):
        self._join(self.old_fusion_member, self.pirate, CUTOFF + ONE_MINUTE)

        self.assertNotIn(
            self.old_fusion_member.email,
            _matched_emails(LOGIC_ALL, _membership_since_rule(self.fusion, CUTOFF_AS_SUBMITTED)),
            "This person's Fusion membership is too old and only their Pirate membership "
            "is recent enough, so neither membership satisfies both clauses.",
        )

    def test_all_of_two_types_matches_a_person_holding_both(self):
        self._join(self.cutoff_fusion_member, self.pirate, CUTOFF)

        self.assertEqual(
            _matched_emails(
                LOGIC_ALL, _membership_rule(self.fusion), _membership_rule(self.pirate)
            ),
            {self.cutoff_fusion_member.email},
            "Two conditions must be free to match two different membership rows.",
        )

    def test_none_logic_excludes_members_but_keeps_everyone_else(self):
        self.assertEqual(
            _matched_emails(LOGIC_NONE, _membership_rule(self.fusion)),
            {self.non_member.email, self.pirate_member.email},
        )

    def test_person_with_two_matching_memberships_is_listed_once(self):
        self._join(self.cutoff_fusion_member, self.fusion, CUTOFF + ONE_MINUTE)
        people_filter = PeopleFilter.objects.create(
            name="Recent Fusion members",
            criteria={
                "logic": LOGIC_ALL,
                "rules": [_membership_since_rule(self.fusion, CUTOFF_AS_SUBMITTED)],
            },
        )

        self.assertEqual(
            list(people_filter.apply(Person.objects.all()).values_list("email", flat=True)),
            [self.cutoff_fusion_member.email],
        )

    def test_rule_with_an_unusable_time_matches_nobody(self):
        self.assertEqual(
            _matched_emails(LOGIC_ALL, _membership_since_rule(self.fusion, "")),
            set(),
            "A rule saved without a time cannot be evaluated, and must not fall back "
            "to matching every member of the type.",
        )


class MembershipFilterAdminFormTest(django.test.TestCase):
    """Saves filters through the real admin form, which is what the query builder submits to."""

    TYPE_ERROR = "Choose a membership type for each membership condition."
    TIME_ERROR = "Choose a time for each &quot;has membership since&quot; condition."

    def setUp(self):
        self.client.force_login(
            Person.objects.create_user(
                email="filter.editor@example.com",
                password="password",
                is_staff=True,
                is_superuser=True,
            )
        )
        self.fusion, _ = MembershipType.objects.get_or_create(name="Fusion Party")
        self.add_url = reverse("admin:underground_crm_peoplefilter_add")
        self.filter_name = "Members who joined this financial year"

    def _save(self, *rules: dict):
        return self.client.post(
            self.add_url,
            {
                "name": self.filter_name,
                "description": "",
                "criteria": json.dumps({"logic": LOGIC_ALL, "rules": list(rules)}),
            },
        )

    def _was_saved(self) -> bool:
        return PeopleFilter.objects.filter(name=self.filter_name).exists()

    def test_saves_a_complete_since_condition(self):
        self._save(_membership_since_rule(self.fusion, CUTOFF_AS_SUBMITTED))

        self.assertTrue(self._was_saved())

    def test_rejects_a_condition_without_a_type(self):
        rule_without_type = {**_membership_rule(self.fusion), "value": ""}

        self.assertContains(self._save(rule_without_type), self.TYPE_ERROR)
        self.assertFalse(self._was_saved())

    def test_rejects_a_since_condition_without_a_time(self):
        self.assertContains(self._save(_membership_since_rule(self.fusion, "")), self.TIME_ERROR)
        self.assertFalse(self._was_saved())

    def test_rejects_an_incomplete_condition_inside_a_nested_group(self):
        nested_group = {
            "logic": LOGIC_NONE,
            "rules": [{**_membership_rule(self.fusion), "value": ""}],
        }

        self.assertContains(self._save(nested_group), self.TYPE_ERROR)
        self.assertFalse(self._was_saved())

    def test_form_offers_the_membership_types_to_the_query_builder(self):
        response = self.client.get(self.add_url)
        widget = response.context["adminform"].form.fields["criteria"].widget

        self.assertIsInstance(widget, QueryBuilderWidget)
        self.assertIn(
            {"id": str(self.fusion.pk), "label": self.fusion.name},
            json.loads(widget.attrs["data-membership-types"]),
        )
