import datetime
import json
import zoneinfo

import django.test
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from underground_crm.models import Membership, MembershipType, PeopleFilter
from underground_crm.models.filter import (
    LOGIC_ALL,
    MEMBERSHIP_FIELD,
    MEMBERSHIP_START_DATE_KEY,
    MEMBERSHIP_STARTED_BEFORE_OPERATOR,
    MEMBERSHIP_STARTED_BY_OPERATOR,
)

Person = get_user_model()

MELBOURNE = zoneinfo.ZoneInfo("Australia/Melbourne")

# The last day of the 2025–26 financial year, as the query builder's date input submits it.
START_DATE = datetime.date(2026, 6, 30)
START_DATE_AS_SUBMITTED = START_DATE.isoformat()
ONE_DAY = datetime.timedelta(days=1)

# Just after midnight in Melbourne it is still the previous day in UTC, so a membership
# started then on START_DATE would be misread as starting the day before if the
# comparison used UTC rather than the site's time zone. Late in the evening marks the
# other end of a day.
JUST_AFTER_MIDNIGHT = datetime.time(0, 15)
LATE_EVENING = datetime.time(23, 45)


def _at(day: datetime.date, time_of_day: datetime.time) -> datetime.datetime:
    return datetime.datetime.combine(day, time_of_day, tzinfo=MELBOURNE)


def _start_date_rule(membership_type: MembershipType, operator: str, start_date: str) -> dict:
    return {
        "field": MEMBERSHIP_FIELD,
        "operator": operator,
        "value": str(membership_type.pk),
        MEMBERSHIP_START_DATE_KEY: start_date,
    }


def _matched_emails(*rules: dict) -> set[str]:
    # This stays unsaved because a test may evaluate several filters, and names are unique.
    people_filter = PeopleFilter(
        name="Members by start date", criteria={"logic": LOGIC_ALL, "rules": list(rules)}
    )
    return set(people_filter.apply(Person.objects.all()).values_list("email", flat=True))


class MembershipStartDateFilterTest(django.test.TestCase):
    def setUp(self):
        self.fusion, _ = MembershipType.objects.get_or_create(name="Fusion Party")
        self.pirate, _ = MembershipType.objects.get_or_create(name="Pirate Party")

        self.day_before_member = self._member(
            "nadia.haddad@example.com", self.fusion, _at(START_DATE - ONE_DAY, LATE_EVENING)
        )
        self.early_on_the_day_member = self._member(
            "liam.nguyen@example.com", self.fusion, _at(START_DATE, JUST_AFTER_MIDNIGHT)
        )
        self.late_on_the_day_member = self._member(
            "grace.obrien@example.com", self.fusion, _at(START_DATE, LATE_EVENING)
        )
        self.day_after_member = self._member(
            "arjun.patel@example.com", self.fusion, _at(START_DATE + ONE_DAY, JUST_AFTER_MIDNIGHT)
        )
        self.pirate_member = self._member(
            "freya.lindqvist@example.com", self.pirate, _at(START_DATE - ONE_DAY, LATE_EVENING)
        )

    @staticmethod
    def _member(
        email: str, membership_type: MembershipType, started_at: datetime.datetime
    ) -> Person:
        person = Person.objects.create_user(email=email, password="password")
        # Approved, because only an approved membership is active, and these tests
        # are about start dates rather than approval.
        Membership.objects.create(
            person=person, type=membership_type, started_at=started_at, is_approved=True
        )
        return person

    def _matched(self, operator: str, start_date: str = START_DATE_AS_SUBMITTED) -> set[str]:
        return _matched_emails(_start_date_rule(self.fusion, operator, start_date))

    def test_started_before_excludes_the_date_itself(self):
        self.assertEqual(
            self._matched(MEMBERSHIP_STARTED_BEFORE_OPERATOR),
            {self.day_before_member.email},
            f"Only {self.day_before_member.email} joined before {START_DATE}; "
            f"{self.pirate_member.email} did too, but holds a different type.",
        )

    def test_started_by_includes_the_whole_of_the_date(self):
        self.assertEqual(
            self._matched(MEMBERSHIP_STARTED_BY_OPERATOR),
            {
                self.day_before_member.email,
                self.early_on_the_day_member.email,
                self.late_on_the_day_member.email,
            },
            f"Both people who joined at either end of {START_DATE} must count, and "
            f"{self.day_after_member.email}, who joined just after it, must not.",
        )

    def test_expired_membership_does_not_count(self):
        Membership.objects.filter(person=self.day_before_member).update(
            expires_on=timezone.localdate() - ONE_DAY
        )

        for operator in (MEMBERSHIP_STARTED_BEFORE_OPERATOR, MEMBERSHIP_STARTED_BY_OPERATOR):
            self.assertNotIn(
                self.day_before_member.email,
                self._matched(operator),
                f"{operator}: like every membership condition, this counts active "
                "memberships only.",
            )

    def test_one_membership_must_be_both_of_the_type_and_old_enough(self):
        # The day-after member also holds a Pirate membership from long ago.
        Membership.objects.create(
            person=self.day_after_member,
            type=self.pirate,
            started_at=_at(START_DATE - ONE_DAY, LATE_EVENING),
            is_approved=True,
        )

        self.assertNotIn(
            self.day_after_member.email,
            self._matched(MEMBERSHIP_STARTED_BY_OPERATOR),
            "Only this person's Pirate membership is old enough, so it must not make "
            "their recent Fusion membership count.",
        )

    def test_rule_with_an_unusable_date_matches_nobody(self):
        for operator in (MEMBERSHIP_STARTED_BEFORE_OPERATOR, MEMBERSHIP_STARTED_BY_OPERATOR):
            self.assertEqual(
                self._matched(operator, start_date=""),
                set(),
                f"{operator}: a rule saved without a date cannot be evaluated, and must "
                "not fall back to matching every member of the type.",
            )


class MembershipStartDateAdminFormTest(django.test.TestCase):
    """Saves filters through the real admin form, which is what the query builder submits to."""

    DATE_ERROR = "Choose a start date for each membership condition that needs one."

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
        self.filter_name = "Members who joined by the end of the financial year"

    def _save(self, rule: dict):
        return self.client.post(
            self.add_url,
            {
                "name": self.filter_name,
                "description": "",
                "criteria": json.dumps({"logic": LOGIC_ALL, "rules": [rule]}),
            },
        )

    def _was_saved(self) -> bool:
        return PeopleFilter.objects.filter(name=self.filter_name).exists()

    def test_saves_a_complete_started_by_condition(self):
        self._save(
            _start_date_rule(self.fusion, MEMBERSHIP_STARTED_BY_OPERATOR, START_DATE_AS_SUBMITTED)
        )

        self.assertTrue(self._was_saved())

    def test_rejects_a_started_before_condition_without_a_date(self):
        response = self._save(_start_date_rule(self.fusion, MEMBERSHIP_STARTED_BEFORE_OPERATOR, ""))

        self.assertContains(response, self.DATE_ERROR)
        self.assertFalse(self._was_saved())
