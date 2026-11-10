import datetime
import json
import zoneinfo

import django.test
from django.contrib.auth import get_user_model

from underground_crm.forms.person_filter import PeopleFilterAdminForm
from underground_crm.models import Interaction, PeopleFilter
from underground_crm.models.filter import (
    INTERACTION_BETWEEN_OPERATOR,
    INTERACTION_FIELD,
    INTERACTION_SINCE_OPERATOR,
    INTERACTION_UNTIL_KEY,
    LOGIC_ALL,
    LOGIC_NONE,
)

Person = get_user_model()

MELBOURNE = zoneinfo.ZoneInfo("Australia/Melbourne")

# A phone-banking campaign that ran through July.
CAMPAIGN_START = datetime.date(2026, 7, 1)
CAMPAIGN_END = datetime.date(2026, 7, 31)
ONE_DAY = datetime.timedelta(days=1)
# Late in the evening in Melbourne, when it is still the morning of the same day
# in UTC. An interaction on the last day of the campaign at this time must still
# count as within it, and one on the day before the campaign must not.
EVENING = datetime.time(23, 45)
# Early in the morning in Melbourne, when it is still the previous day in UTC, so
# a date read in UTC would wrongly put this before the campaign started.
EARLY_MORNING = datetime.time(7, 15)


def _at(date: datetime.date, time: datetime.time) -> datetime.datetime:
    return datetime.datetime.combine(date, time, tzinfo=MELBOURNE)


def _since_rule(first_date: datetime.date) -> dict:
    return {
        "field": INTERACTION_FIELD,
        "operator": INTERACTION_SINCE_OPERATOR,
        "value": first_date.isoformat(),
    }


def _between_rule(first_date: datetime.date, last_date: datetime.date) -> dict:
    return {
        "field": INTERACTION_FIELD,
        "operator": INTERACTION_BETWEEN_OPERATOR,
        "value": first_date.isoformat(),
        INTERACTION_UNTIL_KEY: last_date.isoformat(),
    }


def _matched_emails(logic: str, *rules: dict) -> set[str]:
    people_filter = PeopleFilter(
        name="Interactions under test", criteria={"logic": logic, "rules": list(rules)}
    )
    return set(people_filter.apply(Person.objects.all()).values_list("email", flat=True))


class InteractionFilterTest(django.test.TestCase):
    def setUp(self):
        self.never_contacted = self._person("harper.nevercontacted@example.com")
        self.called_before = self._person("quinn.before@example.com")
        self.called_first_morning = self._person("rowan.firstmorning@example.com")
        self.called_last_evening = self._person("ellis.lastevening@example.com")
        self.called_after = self._person("sasha.after@example.com")

        self._interact(self.called_before, _at(CAMPAIGN_START - ONE_DAY, EVENING))
        self._interact(self.called_first_morning, _at(CAMPAIGN_START, EARLY_MORNING))
        self._interact(self.called_last_evening, _at(CAMPAIGN_END, EVENING))
        self._interact(self.called_after, _at(CAMPAIGN_END + ONE_DAY, EARLY_MORNING))

    @staticmethod
    def _person(email: str) -> Person:
        return Person.objects.create_user(email=email, password="password")

    @staticmethod
    def _interact(person: Person, when: datetime.datetime) -> None:
        interaction = Interaction.objects.create(
            person=person, method=Interaction.METHOD_PHONE_CALL, note="Asked about volunteering"
        )
        # created_at is auto_now_add, which overwrites any value given to create().
        Interaction.objects.filter(pk=interaction.pk).update(created_at=when)

    def test_between_includes_both_end_dates_in_the_site_time_zone(self):
        self.assertEqual(
            _matched_emails(LOGIC_ALL, _between_rule(CAMPAIGN_START, CAMPAIGN_END)),
            {self.called_first_morning.email, self.called_last_evening.email},
            "Only the people called during the campaign, including on its first and "
            "last days, should match.",
        )

    def test_since_includes_the_first_date_and_everything_after(self):
        self.assertEqual(
            _matched_emails(LOGIC_ALL, _since_rule(CAMPAIGN_START)),
            {
                self.called_first_morning.email,
                self.called_last_evening.email,
                self.called_after.email,
            },
        )

    def test_none_logic_finds_people_not_contacted_during_the_period(self):
        self.assertEqual(
            _matched_emails(LOGIC_NONE, _between_rule(CAMPAIGN_START, CAMPAIGN_END)),
            {self.never_contacted.email, self.called_before.email, self.called_after.email},
        )

    def test_person_with_several_interactions_in_the_period_is_listed_once(self):
        self._interact(self.called_first_morning, _at(CAMPAIGN_START + ONE_DAY, EVENING))
        people_filter = PeopleFilter.objects.create(
            name="Called during the campaign",
            criteria={"logic": LOGIC_ALL, "rules": [_since_rule(CAMPAIGN_START)]},
        )

        matched = list(people_filter.apply(Person.objects.all()).values_list("email", flat=True))

        self.assertEqual(matched.count(self.called_first_morning.email), 1)

    def test_rule_without_a_valid_date_matches_nobody(self):
        unfinished_rule = {
            "field": INTERACTION_FIELD,
            "operator": INTERACTION_BETWEEN_OPERATOR,
            "value": CAMPAIGN_START.isoformat(),
            INTERACTION_UNTIL_KEY: "",
        }

        self.assertEqual(_matched_emails(LOGIC_ALL, unfinished_rule), set())


class InteractionFilterFormTest(django.test.TestCase):
    """Verifies that the admin form refuses an interaction condition with an unusable period."""

    @staticmethod
    def _form(rule: dict) -> PeopleFilterAdminForm:
        return PeopleFilterAdminForm(
            data={
                "name": "Called during the campaign",
                "description": "",
                "criteria": json.dumps({"logic": LOGIC_ALL, "rules": [rule]}),
            }
        )

    def test_accepts_a_valid_period(self):
        form = self._form(_between_rule(CAMPAIGN_START, CAMPAIGN_END))

        self.assertTrue(form.is_valid(), form.errors)

    def test_rejects_a_missing_start_date(self):
        rule = _since_rule(CAMPAIGN_START) | {"value": ""}

        self.assertIn("criteria", self._form(rule).errors)

    def test_rejects_a_missing_end_date(self):
        rule = _between_rule(CAMPAIGN_START, CAMPAIGN_END) | {INTERACTION_UNTIL_KEY: ""}

        self.assertIn("criteria", self._form(rule).errors)

    def test_rejects_an_end_date_before_the_start_date(self):
        form = self._form(_between_rule(CAMPAIGN_END, CAMPAIGN_START))

        self.assertIn("criteria", form.errors)
